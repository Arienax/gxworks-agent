"""Cross-manual PID supplement for the FX5 knowledge chain.

The FX5 Application manual documents the PID parameter block but explicitly
defers the instruction-level precautions to the FX5 programming manual, and the
programming manual defers the parameter details back to the Application manual.
Only the Application manual used to be registered in the private library, so the
instruction half was genuinely absent.  This module publishes that half from the
newly registered programming manual, and re-states the Application manual's
``s3+20``-``s3+24`` alarm block from its own pages.

Every published value is traced to a chunk or page artifact of a registered
manual; nothing here reads the network, mutates a database, or promotes a
documented statement to executable behaviour.
"""
from __future__ import annotations

import re

METHOD = 'authored_fx5_pid_supplement_v1'

# Registered Application manual: parameter block, alarm block, detail pages.
APPLICATION_MANUAL = 'fx5_application_ag'
APPLICATION_MANUAL_NUMBER = 'JY997D55401'
APPLICATION_REVISION = 'AG'
APPLICATION_SECTION = '36.5'
APPLICATION_ALARM_PAGES = (578, 579, 581, 590)
APPLICATION_PARAMETER_PAGE = 578
# The three instruction-language columns are printed on p576; the programming
# manual pages for PID print the ST call text only.
APPLICATION_LANGUAGE_PAGE = 576
LANGUAGE_WITNESS = ('Ladder diagram', 'Structured text', 'FBD/LD')
RECIPROCAL_WITNESS = ('FX5 User\'s Manual (Application)',)
SAMPLING_TIME_WITNESS = ('Sampling time (TS)', '1 to 32767 (ms)',
                         'It cannot be shorter than operation cycle')
APPLICATION_DETAIL_SECTION = 'Details of parameters'
APPLICATION_DETAIL_WINDOW = (574, 600)

# Programming manual that the Application manual cites for PID precautions.
PROGRAMMING_MANUAL = 'fx5_programming_ab'
PROGRAMMING_MANUAL_NUMBER = 'JY997D55801'
PROGRAMMING_REVISION = 'AB'
PROGRAMMING_OFFICIAL_URL = ('https://www.mitsubishielectric.com/dl/fa/document/manual/'
                            'plcf/jy997d55801/jy997d55801ab.pdf')
PROGRAMMING_SECTION = '10.1 PID Control Loop'
PROGRAMMING_OUTLINE = ('PART 3 CPU MODULE INSTRUCTIONS > 10 PID CONTROL INSTRUCTION > '
                       '10.1 PID Control Loop')
# Guard only: the pages are selected through the outline above, and the window
# proves that no neighbouring instruction (9.1 at p961, 11.1 at p965) slipped in.
PROGRAMMING_PAGE_WINDOW = (962, 964)

OPCODE = 'PID'
DOCUMENTED_MODELS = ('FX5S', 'FX5UJ', 'FX5U', 'FX5UC')

PROGRAMMING_WITNESS = (
    'This instruction executes PID control which changes the output value according to the '
    'input variation',
    'ENO:=PID(EN,s1,s2,s3,d)',
    'Device number storing the target value (SV)',
    'Device number storing the measured value (PV)',
    'Device number storing a parameter',
    'Device number storing the output value (MV)',
    'Only D, SD, R can be used',
    'When setting using a label, use the global label assigned to the device',
)

OPERANDS = (
    ('s1', 'target_value_SV', 'input', -32768, 32767),
    ('s2', 'measured_value_PV', 'input', -32768, 32767),
    ('s3', 'parameter_head_device', 'input', 1, 32767),
    ('d', 'output_value_MV', 'output', -32768, 32767),
)

DEVICE_RESTRICTION = {
    'documented_footnote': 'only_D_SD_R_can_be_used',
    'applies_to_operands': ['s1', 's2', 's3', 'd'],
    'label_note': 'when_setting_using_a_label_use_the_global_label_assigned_to_the_device',
}

DEVICE_OCCUPANCY = {
    'without_auto_tuning': 25,
    'auto_tuning_limit_cycle_method': 29,
    'auto_tuning_step_response_method_b8_off': 25,
    'auto_tuning_step_response_method_b8_on': 28,
    'documented_basis': 'devices_occupied_from_the_s3_head_device',
}
DEVICE_OCCUPANCY_WITNESS = (
    'twenty-five devices are occupied from the head device specified in (s3)',
    'twenty-nine devices are occupied from the head device specified in (s3)',
    'Twenty-eight devices are occupied',
)

CONCURRENCY = {
    'concurrent_loops': 'no_documented_limit',
    'requirement': 'operands_of_each_instruction_must_differ',
}
CONCURRENCY_WITNESS = (
    'Two or more PID instructions can be executed at the same time',
    'operands specified in each instruction are different',
)

# The repeated table header is removed by clean_text's boilerplate pass. Read
# the preserved page text for this field rather than manufacturing a source gap.
ERROR_REGISTER = ['SD0', 'SD8067']
ERROR_TABLE_WITNESS = ('Operation error', 'Error code')
# Two rows are printed inside the PID error table on p963 (verified against the
# PDF itself, so they are not an adjacent-page leak) while their wording names a
# connection number for (s1) and an operand (n).  The PID operand list is
# s1=SV, s2=PV, s3=parameter head, d=MV: there is no connection number and no
# ``n``.  That is a documented internal conflict, so the rows are published as a
# conflict fact with ``documented_conflict_pending_verification`` and their
# original page/section sources kept, never as an applicable PID constraint.
CONFLICT_ROWS = (
    {'code': '2820H',
     'printed_row_token': 'connection_number_specified_by_s1_is_other_than_1_to_8',
     'printed_witness': 'The connection number specified by (s1) is other than 1 to 8',
     'conflict': 's1_is_documented_as_the_target_value_SV_but_the_row_names_a_connection_number',
     'named_operand': 's1',
     'named_but_absent_operand': ''},
    {'code': '2822H',
     'printed_row_token': 'device_number_specified_by_s2_d_or_n_is_outside_the_device_range',
     'printed_witness': 'outside the range of the number of device points',
     'conflict': 'the_row_covers_operand_n_which_is_not_a_pid_operand',
     'named_operand': 'n',
     'named_but_absent_operand': 'n'},
)
CONFLICT_STATUS = 'conflict'
CONFLICT_PAGE = 963
PID_OPERAND_NAMES = ('s1', 's2', 's3', 'd')
PID_ERROR_CODES = (
    ('3500H', 'incorrect_sampling_time', 'Incorrect sampling time'),
    ('3502H', 'incompatible_input_filter_constant', 'Incompatible input filter constant'),
    ('3503H', 'incompatible_proportional_gain', 'Incompatible proportional gain'),
    ('3504H', 'incompatible_integral_time', 'Incompatible integral time'),
    ('3505H', 'incompatible_derivative_gain', 'Incompatible derivative gain'),
    ('3506H', 'incompatible_derivative_time', 'Incompatible derivative time'),
    ('350AH', 'sampling_time_not_greater_than_scan_time', 'Scan time'),
    ('350CH', 'variation_of_measured_value_exceeds_limit',
     'Variation of measured value exceeds limit'),
    ('350DH', 'deviation_exceeds_limit', 'Deviation exceeds limit'),
    ('350EH', 'integral_result_exceeds_limit', 'Integral result exceeds limit'),
    ('350FH', 'derivative_value_exceeds_limit_due_to_derivative_gain',
     'Derivative value exceeds limit due to derivative gain'),
    ('3510H', 'derivative_result_exceeds_limit', 'Derivative result exceeds limit'),
    ('3511H', 'pid_operation_result_exceeds_limit', 'PID operation result exceeds limit'),
    ('3512H', 'output_upper_limit_below_output_lower_limit',
     'PID output upper limit set value'),
    ('3513H', 'abnormal_input_or_output_variation_alarm_set_value',
     'Abnormal PID input variation alarm set value'),
    ('3514H', 'step_response_improper_auto_tuning_result', 'Improper auto tuning result'),
    ('3515H', 'step_response_auto_tuning_operation_direction_mismatch',
     'Auto tuning operation direction mismatch'),
    ('3516H', 'step_response_improper_auto_tuning_operation',
     'Improper auto tuning operation'),
    ('3517H', 'limit_cycle_abnormal_output_set_value_for_auto_tuning',
     'Abnormal output set value for auto tuning'),
    ('3518H', 'limit_cycle_abnormal_pv_threshold_for_auto_tuning', 'Abnormal PV threshold'),
    ('3519H', 'limit_cycle_abnormal_auto_tuning_transfer_status',
     'Abnormal auto tuning transfer status'),
    ('351AH', 'limit_cycle_excessive_auto_tuning_measurement_time',
     'Abnormal result due to excessive auto tuning measurement time'),
    ('351BH', 'limit_cycle_auto_tuning_result_exceeds_proportional_gain',
     'Auto tuning result exceeds proportional gain'),
    ('351CH', 'limit_cycle_auto_tuning_result_exceeds_integral_time',
     'Auto tuning result exceeds integral time'),
    ('351DH', 'limit_cycle_auto_tuning_result_exceeds_derivative_time',
     'Auto tuning result exceeds derivative time'),
    ('351EH', 'step_response_auto_tuning_timeout_after_maximum_ramp',
     'Abnormal timeout time set value after maximum ramp'),
)

CAUTIONS = (
    ('output_value_should_be_an_unlatched_data_register', 'specify an unlatched data register'),
    ('latched_output_value_requires_clearing_the_backup_before_run',
     'make sure to clear the backup contents'),
    ('parameter_head_device_is_occupied_by_the_instruction',
     'devices are occupied from the head device specified in (s3)'),
)

ALARM_BLOCK = {
    'offset_base': 's3_plus_n_parameters_of_the_pid_parameter_block',
    'block_offsets': [20, 21, 22, 23, 24],
    'fields': {
        's3_plus_20': {'name': 'input_variation_incremental_alarm_set_value',
                       'range': {'minimum': 0, 'maximum': 32767},
                       'active_when': 'operation_setting_b1_is_on'},
        's3_plus_21': {'name': 'input_variation_decremental_alarm_set_value',
                       'range': {'minimum': 0, 'maximum': 32767},
                       'active_when': 'operation_setting_b1_is_on'},
        's3_plus_22': {'name': 'output_variation_incremental_alarm_set_value',
                       'range': {'minimum': 0, 'maximum': 32767},
                       'active_when': 'operation_setting_b2_on_and_b5_off',
                       'alternate_role': {
                           'name': 'output_upper_limit_set_value',
                           'range': {'minimum': -32768, 'maximum': 32767},
                           'active_when': 'operation_setting_b2_off_and_b5_on'}},
        's3_plus_23': {'name': 'output_variation_decremental_alarm_set_value',
                       'range': {'minimum': 0, 'maximum': 32767},
                       'active_when': 'operation_setting_b2_on_and_b5_off',
                       'alternate_role': {
                           'name': 'output_lower_limit_set_value',
                           'range': {'minimum': -32768, 'maximum': 32767},
                           'active_when': 'operation_setting_b2_off_and_b5_on'}},
        's3_plus_24': {'name': 'alarm_output',
                       'bits': {'b0': 'input_variation_incremental_exceeded',
                                'b1': 'input_variation_decremental_exceeded',
                                'b2': 'output_variation_incremental_exceeded',
                                'b3': 'output_variation_decremental_exceeded'},
                       'active_when': 'documented_activation_bits_of_operation_setting'},
    },
    'occupancy_rule': 'used_only_when_act_bits_b1_b2_or_b5_are_on',
    'mutual_exclusion': 'b2_and_b5_must_not_be_on_at_the_same_time',
    'variation_definition': 'current_value_minus_previous_value',
    'alarm_output_documented_as': 'each_bit_of_s3_plus_24_turns_on_as_a_warning_output',
}

ALARM_BLOCK_WITNESS = (
    'Do not set b2 and b5 to ON at the same time',
    'the input variation and the output variation can be checked',
    'Variation means (Current value) - (Previous value)',
    'Alarm output (s3)+24',
    'each bit of (s3) +24 turns ON as a warning output',
    'b2 and b5 of the operation setting (ACT) ((s3)+1) are "1" and "0"',
)

ALARM_ACTIVATION_WITNESS = (
    '(s3)+20 to +24 become used only if b1, b2, or b5',
)

APPLICATION_DETAIL_WITNESS = (
    ('pid_alarm_setting',
     ('If b1 and b2 in (s3) +1 are turned ON, the input variation and the output '
      'variation can be checked',
      'Input variation (incremental) alarm'),
     'alarm_setting_for_input_and_output_variation'),
    ('overshoot_suppression',
     ('Set the overshoot suppression processing',
      'similar performance to the FX3'),
     'overshoot_suppression_setting_on_b7'),
    ('output_limits',
     ('Upper and lower limits for output value',
      'make sure to set (s3)+1, b2 to OFF'),
     'output_upper_and_lower_limits'),
    ('hunting_suppression',
     ('Hunting suppression setting',
      'maximum ramp (R) can be obtained correctly'),
     'hunting_suppression_setting_on_b8'),
    ('input_filter_constant',
     ('Input filter constant (s3)+2',
      'Setting range: 0 to 99'),
     'input_filter_constant_on_s3_plus_2'),
)


def _plain(value):
    """Keep values inside the typed-fact vocabulary (tuples become lists)."""
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    return value


def _rows(source, sql, parameters=()):
    cursor = source.execute(sql, parameters)
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _normalized(text):
    return re.sub(r'\s+', ' ', text or '').strip().casefold()


def _contains(text, phrase):
    return _normalized(phrase) in _normalized(text)


def _verify(text, phrases, label):
    missing = [phrase for phrase in phrases if not _contains(text, phrase)]
    if missing:
        raise ValueError('PID supplement source changed: %s is missing documented text: %s'
                         % (label, '; '.join(missing)))


def _manual_meta(source, manual_id):
    rows = _rows(source, 'SELECT manual_id, manual_number, revision, title, official_url '
                         'FROM manuals WHERE manual_id=?', (manual_id,))
    if not rows:
        raise ValueError('PID supplement source is missing manual metadata: ' + manual_id)
    return rows[0]


def _covering_chunks(source, manual_id, page):
    """Chunks whose page range contains the page; a chunk may span many pages."""
    return _rows(source, 'SELECT chunk_uid, manual_id, manual_number, revision, pdf_page, '
                         'pdf_page_end, chunk_type FROM chunks WHERE manual_id=? AND '
                         'pdf_page<=? AND pdf_page_end>=? ORDER BY id', (manual_id, page, page))


def _chunk_reference(row):
    return {'id': row['chunk_uid'], 'source_kind': 'chunk', 'manual_id': row['manual_id'],
            'manual_number': row['manual_number'], 'revision': row['revision'],
            'pdf_pages': list(range(row['pdf_page'],
                                    (row['pdf_page_end'] or row['pdf_page']) + 1))}


def _artifact_reference(row, manual_number, revision):
    return {'id': 'artifact:%s:p%s' % (row['manual_id'], row['pdf_page']),
            'source_kind': 'page_artifact', 'manual_id': row['manual_id'],
            'manual_number': manual_number, 'revision': revision,
            'pdf_pages': [row['pdf_page']]}


def _fact(identity, dimension, value, references, models, *, languages=None,
          status='source_verified', note=None):
    scope = {'models': list(models), 'forms': [OPCODE],
             'languages': list(languages or ('ladder', 'st', 'fbd'))}
    fact = {'id': identity, 'dimension': dimension, 'value': _plain(value),
            'status': status,
            'verification': 'source_checked' if status == 'source_verified' else 'not_performed',
            'sources': [dict(reference) for reference in references], 'scope': scope}
    if note:
        fact['note'] = note
    return fact


def _page_text(source, manual_id, page):
    rows = _rows(source, 'SELECT pdf_page, section, clean_text AS text FROM page_artifacts '
                         'WHERE manual_id=? AND pdf_page=?', (manual_id, page))
    if not rows:
        raise ValueError('PID supplement source has no page artifact: %s p%s'
                         % (manual_id, page))
    return rows[0]


def _page_references(source, manual_id, pages, manual_number, revision):
    """One reference per page: the covering chunk, else the preserved page artifact."""
    references = []
    uncovered = []
    for page in pages:
        chunks = _covering_chunks(source, manual_id, page)
        if chunks:
            references.extend(_chunk_reference(chunk) for chunk in chunks)
        else:
            uncovered.append(page)
            references.append(_artifact_reference(_page_text(source, manual_id, page),
                                                  manual_number, revision))
    return references, uncovered


def _section_token(outline):
    '''A typed-safe token for a documented outline path.'''
    token = re.sub(r'[^A-Za-z0-9]+', '_', outline or '').strip('_').lower()
    return re.sub(r'_+', '_', token)


def _section_pages(source, manual_id, section, outline, window):
    """Pages owned by one instruction section, verified against its outline.

    A page range alone can swallow the next instruction (the PID table ends at
    p964 and 11.1 starts at p965), so the selection is made through the outline
    and every selected page is re-checked for the owning section.
    """
    pages = _rows(source, 'SELECT pdf_page, section, outline_path FROM page_artifacts '
                          'WHERE manual_id=? AND outline_path=? AND section=? ORDER BY pdf_page',
                  (manual_id, outline, section))
    if not pages:
        raise ValueError('PID supplement source has no pages for %s %s'
                         % (manual_id, outline))
    numbers = [row['pdf_page'] for row in pages]
    if numbers != list(range(numbers[0], numbers[-1] + 1)):
        raise ValueError('PID supplement source has a gap inside %s: %s'
                         % (outline, numbers))
    if numbers[0] < window[0] or numbers[-1] > window[1]:
        raise ValueError('PID supplement section moved outside the expected window: %s'
                         % numbers)
    neighbours = _rows(source,
                       'SELECT pdf_page, section, outline_path FROM page_artifacts '
                       'WHERE manual_id=? AND pdf_page BETWEEN ? AND ? AND outline_path<>? '
                       'ORDER BY pdf_page',
                       (manual_id, numbers[0], numbers[-1], outline))
    if neighbours:
        raise ValueError('PID supplement section would include a neighbouring instruction: %s'
                         % [(row['pdf_page'], row['outline_path']) for row in neighbours])
    return numbers


def _chunks_of_section(source, manual_id, outline, section, pages):
    """Chunks that cover the section pages and belong to the same outline."""
    found = []
    for page in pages:
        for chunk in _covering_chunks(source, manual_id, page):
            row = _rows(source, 'SELECT section, outline_path FROM chunks WHERE chunk_uid=?',
                        (chunk['chunk_uid'],))
            if not row or row[0]['outline_path'] != outline:
                raise ValueError('PID supplement chunk crosses an instruction boundary: %s'
                                 % chunk['chunk_uid'])
            found.append(chunk['chunk_uid'])
    return sorted(set(found))


def _section_text(source, manual_id, pages):
    return ' '.join(_page_text(source, manual_id, page)['text'] or '' for page in pages)


def _language_facts(references):
    languages = {'ladder': True, 'st': True, 'fbd_ld': True}
    facts = [_fact('fx5pid:languages@a:' + APPLICATION_MANUAL,
                   'identity.language_availability',
                   {'documented_languages': sorted(languages),
                    'availability': dict(languages),
                    'documented_as': 'ladder_diagram_structured_text_fbd_ld_columns',
                    'documented_page': APPLICATION_LANGUAGE_PAGE,
                    'unavailable_languages': []},
                   references, DOCUMENTED_MODELS, languages=['fbd', 'st'])]
    for language, column in (('ladder', 'ladder_diagram'), ('st', 'structured_text'),
                             ('fbd', 'fbd_ld')):
        facts.append(_fact('fx5pid:language_%s@a:%s' % (language, APPLICATION_MANUAL),
                           'identity.language_availability',
                           {'language': language, 'available': True,
                            'documented_column': column,
                            'documented_page': APPLICATION_LANGUAGE_PAGE,
                            'documented_as': 'ladder_diagram_structured_text_fbd_ld_columns'},
                           references, DOCUMENTED_MODELS, languages=[language]))
    return facts


def _signature_fact(references):
    parameters = [{'name': 'EN', 'type': {'kind': 'bit'}, 'direction': 'input'}]
    for name, _role, direction, _low, _high in OPERANDS:
        parameters.append({'name': name, 'type': {'kind': 'any', 'bits': 16},
                           'direction': direction})
    parameters.append({'name': 'ENO', 'type': {'kind': 'bit'}, 'direction': 'output'})
    return _fact('fx5pid:st_signature@p:' + PROGRAMMING_MANUAL, 'parameters.st_signature',
                 {'documented_call': 'ENO:=PID(EN,s1,s2,s3,d)',
                  'argument_order': ['EN', 's1', 's2', 's3', 'd', 'ENO'],
                  'parameters': parameters,
                  'returns': {'type': {'kind': 'bit'}, 'role': 'ENO_execution_status'},
                  'enable': True,
                  'device_restriction': DEVICE_RESTRICTION},
                 references, DOCUMENTED_MODELS, languages=['st'])


def _operand_fact(references):
    operands = []
    for name, role, direction, low, high in OPERANDS:
        operands.append({'name': name, 'role': role, 'direction': direction,
                         'data_type': {'kind': 'any', 'bits': 16},
                         'label_type': {'kind': 'any', 'bits': 16},
                         'documented_range': {'minimum': low, 'maximum': high},
                         'range_basis': 'documented_setting_data'})
    return _fact('fx5pid:operands@p:' + PROGRAMMING_MANUAL, 'parameters.typed_operands',
                 {'operands': operands,
                  'st_call': 'ENO:=PID(EN,s1,s2,s3,d)',
                  'device_restriction': DEVICE_RESTRICTION,
                  'documented_as': 'descriptions_ranges_and_data_types'},
                 references, DOCUMENTED_MODELS)


def _cross_reference_fact(programming_references, application_references):
    return _fact('fx5pid:cross_manual_reference@p:' + PROGRAMMING_MANUAL,
                 'identity.cross_manual_reference',
                 {'cited_manual_number': PROGRAMMING_MANUAL_NUMBER,
                  'cited_manual_revision': PROGRAMMING_REVISION,
                  'cited_from_section': APPLICATION_SECTION,
                  'deferral': 'pid_instruction_precautions_are_in_the_programming_manual',
                  'reciprocal_deferral':
                      'pid_parameter_details_are_in_the_fx5_users_manual_application',
                  'reciprocal_manual_number': APPLICATION_MANUAL_NUMBER,
                  'reciprocal_section': '36_5'},
                 list(programming_references) + list(application_references),
                 DOCUMENTED_MODELS)


def _alarm_block_fact(application_references, pages):
    value = dict(ALARM_BLOCK)
    value['documented_pages'] = list(pages)
    return _fact('fx5pid:alarm_block@a:' + APPLICATION_MANUAL,
                 'parameters.pid_alarm_block', value,
                 application_references, DOCUMENTED_MODELS)


def _detail_facts(source, details, manual_number, revision):
    facts = []
    for name, witnesses, documented_as in APPLICATION_DETAIL_WITNESS:
        rows = [row for row in details if _contains(row['text'], witnesses[0])]
        if not rows:
            continue
        _verify(' '.join(row['text'] or '' for row in rows), witnesses,
                'application detail ' + name)
        pages = [row['pdf_page'] for row in rows]
        references, uncovered = _page_references(source, APPLICATION_MANUAL, pages,
                                                 manual_number, revision)
        facts.append(_fact('fx5pid:detail_%s@a:%s' % (name, APPLICATION_MANUAL),
                           'parameters.pid_detail_block',
                           {'detail': documented_as,
                            'documented_pages': pages,
                            'pages_without_chunks': uncovered,
                            'indexed_in_chunk': not uncovered},
                           references, DOCUMENTED_MODELS))
    return facts


def reviewed_fx5_pid_supplement(source, entries=None):
    """Return typed FX5 PID supplement facts keyed by (target_model, opcode)."""
    application_meta = _manual_meta(source, APPLICATION_MANUAL)
    programming_meta = _manual_meta(source, PROGRAMMING_MANUAL)
    if programming_meta['manual_number'] != PROGRAMMING_MANUAL_NUMBER:
        raise ValueError('PID supplement source has an unexpected programming manual: %s'
                         % programming_meta['manual_number'])
    if (programming_meta['revision'] or '').upper() != PROGRAMMING_REVISION:
        raise ValueError('PID supplement source has an unexpected revision: %s'
                         % programming_meta['revision'])

    programming_pages = _section_pages(source, PROGRAMMING_MANUAL, PROGRAMMING_SECTION,
                                       PROGRAMMING_OUTLINE, PROGRAMMING_PAGE_WINDOW)
    programming_chunks = _chunks_of_section(source, PROGRAMMING_MANUAL, PROGRAMMING_OUTLINE,
                                            PROGRAMMING_SECTION, programming_pages)
    programming_text = _section_text(source, PROGRAMMING_MANUAL, programming_pages)
    _verify(programming_text, PROGRAMMING_WITNESS, PROGRAMMING_MANUAL)
    _verify(programming_text, DEVICE_OCCUPANCY_WITNESS, PROGRAMMING_MANUAL + ' occupancy')
    _verify(programming_text, CONCURRENCY_WITNESS, PROGRAMMING_MANUAL + ' concurrency')
    _verify(programming_text, RECIPROCAL_WITNESS, PROGRAMMING_MANUAL + ' reciprocal deferral')
    _verify(programming_text, ERROR_TABLE_WITNESS, PROGRAMMING_MANUAL + ' error table')
    original_error_page = _rows(source,
        'SELECT plain_text,layout_text FROM page_artifacts WHERE manual_id=? AND pdf_page=963',
        (PROGRAMMING_MANUAL,))[0]
    original_error_text = original_error_page['plain_text'] + '\n' + original_error_page['layout_text']
    if not re.search(r'SD0\s*/\s*SD8067', original_error_text):
        raise ValueError('PID error-register table header is missing from the preserved source page')
    for code, _meaning, witness in PID_ERROR_CODES:
        if not _contains(programming_text, witness):
            raise ValueError('PID supplement source changed: %s error table lost %s'
                             % (PROGRAMMING_MANUAL, code))
    for row in CONFLICT_ROWS:
        if not _contains(programming_text, row['printed_witness']):
            raise ValueError('PID supplement source changed: %s lost the conflicting row %s'
                             % (PROGRAMMING_MANUAL, row['code']))
    for name, _role, _direction, _low, _high in OPERANDS:
        if not _contains(programming_text, 'Device number storing'):
            raise ValueError('PID supplement source changed: %s lost the operand table'
                             % PROGRAMMING_MANUAL)
        break
    for name, witness in CAUTIONS:
        if not _contains(programming_text, witness):
            raise ValueError('PID supplement source changed: %s caution lost %s'
                             % (PROGRAMMING_MANUAL, name))

    application_text = _section_text(source, APPLICATION_MANUAL, APPLICATION_ALARM_PAGES)
    _verify(application_text, ALARM_BLOCK_WITNESS, APPLICATION_MANUAL + ' alarm block')
    _verify(application_text, SAMPLING_TIME_WITNESS,
            APPLICATION_MANUAL + ' sampling time row')
    _verify(application_text, ALARM_ACTIVATION_WITNESS, APPLICATION_MANUAL + ' alarm activation')

    programming_references, programming_uncovered = _page_references(
        source, PROGRAMMING_MANUAL, programming_pages, PROGRAMMING_MANUAL_NUMBER,
        PROGRAMMING_REVISION)
    application_references, application_uncovered = _page_references(
        source, APPLICATION_MANUAL, APPLICATION_ALARM_PAGES, APPLICATION_MANUAL_NUMBER,
        APPLICATION_REVISION)
    parameter_references, _parameter_uncovered = _page_references(
        source, APPLICATION_MANUAL, (APPLICATION_PARAMETER_PAGE,), APPLICATION_MANUAL_NUMBER,
        APPLICATION_REVISION)
    conflict_references, _conflict_uncovered = _page_references(
        source, PROGRAMMING_MANUAL, (CONFLICT_PAGE,), PROGRAMMING_MANUAL_NUMBER,
        PROGRAMMING_REVISION)
    language_references, _language_uncovered = _page_references(
        source, APPLICATION_MANUAL, (APPLICATION_LANGUAGE_PAGE,), APPLICATION_MANUAL_NUMBER,
        APPLICATION_REVISION)
    _verify(_page_text(source, APPLICATION_MANUAL, APPLICATION_LANGUAGE_PAGE)['text'],
            LANGUAGE_WITNESS, APPLICATION_MANUAL + ' language columns')

    details = _rows(source, 'SELECT manual_id, pdf_page, section, clean_text AS text FROM '
                            'page_artifacts WHERE manual_id=? AND section=? AND pdf_page '
                            'BETWEEN ? AND ? ORDER BY pdf_page',
                    (APPLICATION_MANUAL, APPLICATION_DETAIL_SECTION,
                     APPLICATION_DETAIL_WINDOW[0], APPLICATION_DETAIL_WINDOW[1]))
    detail_pages = [row['pdf_page'] for row in details]
    detail_uncovered = []
    for page in detail_pages:
        if not _covering_chunks(source, APPLICATION_MANUAL, page):
            detail_uncovered.append(page)

    base_facts = [
        _fact('fx5pid:form@p:' + PROGRAMMING_MANUAL, 'identity.form',
              {'mnemonic': OPCODE,
               'instruction_family': 'analog_pid_control',
               'callable_kind': 'analog_instruction',
               'documented_section': '10.1',
               'documented_forms': ['PID'],
               'other_forms': [],
               'language_availability': {'ladder': True, 'st': True, 'fbd_ld': True},
               'language_availability_source': 'application_manual_p576_columns',
               'cpu_scope': list(DOCUMENTED_MODELS),
               'cpu_module_only': False,
               'axis_grouped': False,
               'supplement_of': 'fx5_pid_form_published_by_the_fx5_behavior_review'},
              programming_references, DOCUMENTED_MODELS),
        _signature_fact(programming_references),
        _operand_fact(programming_references),
        _cross_reference_fact(programming_references, application_references),
        _alarm_block_fact(application_references, APPLICATION_ALARM_PAGES),
        _fact('fx5pid:timing@p:' + PROGRAMMING_MANUAL, 'execution.timing',
              {'operation': 'pid_control',
               'output_stored_to_d_at_each_sampling_time': True,
               'sampling_time_parameter': 's3',
               'sampling_time_documented_range_ms': {'minimum': 1, 'maximum': 32767},
               'sampling_time_range_documented_in':
                   'application_manual_36_5_parameter_table',
               'sampling_time_constraint':
                   'cannot_be_shorter_than_the_operation_cycle_of_the_controller',
               'measured_value_is_the_input_of_the_operation': True},
              list(programming_references) + list(parameter_references),
              DOCUMENTED_MODELS),
        _fact('fx5pid:occupancy@p:' + PROGRAMMING_MANUAL, 'resources.device_occupancy',
              dict(DEVICE_OCCUPANCY, **CONCURRENCY), programming_references,
              DOCUMENTED_MODELS),
        _fact('fx5pid:operation_error@p:' + PROGRAMMING_MANUAL, 'constraints.operation_error',
              {'error_register': ERROR_REGISTER,
               'error_register_documented': True,
               'error_table_heading': 'operation_error_with_an_error_code_column',
               'codes': {code: meaning for code, meaning, _witness in PID_ERROR_CODES},
               'documented_as': 'operation_error_table',
               'documented_section_token': _section_token(PROGRAMMING_OUTLINE),
               'documented_pages': list(programming_pages),
               'documented_conflict_codes': [row['code'] for row in CONFLICT_ROWS],
               'documented_conflict_fact_id_token':
                   'fx5pid_conflict_p_' + PROGRAMMING_MANUAL,
               'pending_conflict_rows_published_as_codes': 0},
              programming_references, DOCUMENTED_MODELS),
        _fact('fx5pid:conflict@p:' + PROGRAMMING_MANUAL, 'constraints.documented_conflict',
              {'manual_number': PROGRAMMING_MANUAL_NUMBER,
               'revision': PROGRAMMING_REVISION,
               'section_number': '10_1',
               'section_token': _section_token(PROGRAMMING_OUTLINE),
               'documented_page': CONFLICT_PAGE,
               'printed_inside_the_pid_error_table': True,
               'page_window_artefact': False,
               'classification': 'documented_internal_parameter_conflict_pending_review',
               'pid_operand_names': list(PID_OPERAND_NAMES),
               'entries': [{'code': row['code'],
                            'printed_row_token': row['printed_row_token'],
                            'conflict': row['conflict'],
                            'named_operand': row['named_operand'],
                            'named_but_absent_operand': row['named_but_absent_operand'],
                            'applicable_as_pid_constraint': False}
                           for row in CONFLICT_ROWS],
               'counter_evidence': 'pid_operands_are_s1_SV_s2_PV_s3_parameter_head_d_MV',
               'resolution': 'pending_review_keep_both_documented_readings',
               'native_experiment': 'not_performed'},
              conflict_references, DOCUMENTED_MODELS,
              status=CONFLICT_STATUS,
              note='documented rows conflict with the documented PID operand list; '
                   'both readings are kept and the rows are not published as '
                   'applicable PID constraints'),
        _fact('fx5pid:caution@p:' + PROGRAMMING_MANUAL, 'constraints.documented_caution',
              {'cautions': [name for name, _witness in CAUTIONS],
               'documented_as': 'precautions'},
              programming_references, DOCUMENTED_MODELS),
        _fact('fx5pid:effects@p:' + PROGRAMMING_MANUAL, 'effects.result_rule',
              {'output': 'output_value_MV_written_to_d',
               'update_rate': 'each_sampling_time',
               'operation': 'pid_control_of_output_value',
               'behavior_keys_published': 0},
              programming_references, DOCUMENTED_MODELS),
    ]
    base_facts.extend(_language_facts(language_references))
    base_facts.extend(_detail_facts(source, details, APPLICATION_MANUAL_NUMBER,
                                    APPLICATION_REVISION))

    facts = {}
    for model in DOCUMENTED_MODELS:
        model_facts = []
        for fact in base_facts:
            clone = dict(fact)
            clone['scope'] = dict(fact['scope'], models=[model])
            model_facts.append(clone)
        facts[(model, OPCODE)] = model_facts

    receipt = {
        'method': METHOD,
        'opcode': OPCODE,
        'models': list(DOCUMENTED_MODELS),
        'counts': {'facts': sum(len(rows) for rows in facts.values()),
                   'owner_keys': len(facts),
                   'by_dimension': _count_by(facts, 'dimension'),
                   'by_status': _count_by(facts, 'status')},
        'cross_manual_gap': {
            'was_registered_locally': False,
            'manual_title': programming_meta['title'],
            'manual_number': programming_meta['manual_number'],
            'revision': programming_meta['revision'],
            'official_url': programming_meta['official_url'] or PROGRAMMING_OFFICIAL_URL,
            'documented_section': PROGRAMMING_SECTION,
            'pdf_pages': list(programming_pages),
            'pages_without_chunks': programming_uncovered,
            'cited_by': {'manual_id': APPLICATION_MANUAL,
                         'manual_number': application_meta['manual_number'],
                         'section': APPLICATION_SECTION,
                         'pages': list(APPLICATION_ALARM_PAGES[:2])},
            'recovered_topics': ['instruction_precautions', 'device_occupancy',
                                 'concurrent_loops', 'operation_error_codes',
                                 'applicable_device_footnote',
                                 'occupancy_per_autotuning_mode'],
        },
        'error_table_binding': {
            'manual_id': PROGRAMMING_MANUAL,
            'section': PROGRAMMING_SECTION,
            'outline': PROGRAMMING_OUTLINE,
            'pages': list(programming_pages),
            'chunks': programming_chunks,
            'published_codes': [code for code, _meaning, _witness in PID_ERROR_CODES],
            'documented_conflict_codes': [row['code'] for row in CONFLICT_ROWS],
            'documented_conflict_fact_id': 'fx5pid:conflict@p:' + PROGRAMMING_MANUAL,
            'documented_conflict_fact_id_token':
                'fx5pid_conflict_p_' + PROGRAMMING_MANUAL,
            'documented_conflict_status': CONFLICT_STATUS,
            'binding_rule': 'page_outline_must_equal_the_instruction_section',
        },
        'application_alarm_block': {
            'manual_id': APPLICATION_MANUAL,
            'manual_number': application_meta['manual_number'],
            'documented_pages': list(APPLICATION_ALARM_PAGES),
            'pages_without_chunks': application_uncovered,
            'offsets': ALARM_BLOCK['block_offsets'],
            'note': ('s3+20 to s3+24 are documented and indexed locally; only the '
                     'instruction-level precautions came from the cross-manual'),
        },
        'chunk_coverage': {
            'application_detail_pages': detail_pages,
            'application_detail_pages_without_chunks': detail_uncovered,
            'coverage_rule': 'page_is_covered_when_a_chunk_page_range_contains_it',
        },
        'guarantees': {'database_mutated': False, 'pdf_read_at_runtime': False,
                       'network_at_runtime': False, 'manual_prose_published': False,
                       'executable_behavior_keys': 0, 'call_available_promotions': 0,
                       'cpu_widening': 0},
        'source_policy': {'registered_manuals_only': True,
                          'official_urls_only': True,
                          'manual_metadata_source': 'manuals table of the index'},
    }
    return facts, receipt


def _count_by(facts, key):
    counts = {}
    for rows in facts.values():
        for row in rows:
            counts[row[key]] = counts.get(row[key], 0) + 1
    return counts

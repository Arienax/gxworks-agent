"""Cross-manual call-parameter supplement for SP.ECPRTCL / GP.ECPRTCL.

``SP.ECPRTCL`` (predefined protocol execution on the CPU built-in Ethernet port)
is documented in the QnUCPU and LCPU built-in-Ethernet manuals only as a short
"differences" table: both manuals state that its usage is the same as
``GP.ECPRTCL`` of the Ethernet interface module and defer the actual operand,
control-data and error documentation to that module manual.  Neither module
manual was registered in the private index, so ``GP.ECPRTCL`` could not be
found in ``instructions`` or in any chunk.

This module publishes the call-parameter half from the two module manuals
(SH080009ENG Rev.Y for the Q path, SH081105ENG Rev.G for the L path) together
with the documented ``SP.ECPRTCL`` overrides, so a consumer never has to copy
the module ``Un`` form across.  It reads only the index of already-registered
manuals; it neither reads a PDF, nor touches the network, nor writes a database.
"""
from __future__ import annotations

import re

METHOD = 'authored_ecprtcl_supplement_v1'
OPCODE = 'SP.ECPRTCL'
BASE_OPCODE = 'GP.ECPRTCL'

# Documented SP.ECPRTCL differences (identical wording in both CPU manuals).
DIFFERENCES = (
    {'item': 'setting_data_Un', 'difference': 'U0_dummy_must_be_specified',
     'replacement_note': 'no_change_required_when_replacing_the_module_GP_ECPRTCL'},
    {'item': 'setting_data_n1',
     'difference': 'connection_number_set_in_the_open_setting_of_the_builtin_ethernet_port_setting'},
    {'item': 'protocol_execution_status',
     'difference': 'checked_in_the_start_device_to_store_predefined_protocol'},
    {'item': 'pairing_open', 'difference': 'not_available_in_the_cpu_module',
     'replacement_note': 'one_connection_per_communication'},
    {'item': 'cancellation_of_protocol_execution',
     'difference': 'start_device_to_store_predefined_protocol_offset_plus_18',
     'replacement_note': 'not_the_module_buffer_memory_cancellation_area'},
)

SP_START_DEVICE = {
    'name': 'start_device_to_store_predefined_protocol',
    'area_words': 19,
    'available_devices': ['D', 'W', 'R', 'ZR'],
    'offsets': {
        '0': {'name': 'protocol_execution_status',
              'values': {'0': 'not_executed', '1': 'waiting_for_data_to_be_sent',
                         '2': 'data_being_sent', '3': 'waiting_for_data_to_be_received',
                         '5': 'execution_completed'}},
        '1_to_16': {'name': 'received_data_verification_result',
                    'bit_fields': {'b0_to_b7': 'element_number_where_the_verification_did_not_match',
                                   'b8_to_b15': 'cause_of_mismatch_verification_result_code'}},
        '17': {'name': 'number_of_protocol_executions', 'range': {'minimum': 0, 'maximum': 65535}},
        '18': {'name': 'protocol_cancellation_specification',
               'values': {'0': 'no_cancellation_requested', '1': 'cancellation_requested_set_by_users',
                          '2': 'cancellation_completed_set_by_the_system'}},
    },
}

GP_SETTING_DATA = (
    {'name': 'Un', 'role': 'start_io_number_of_the_module', 'direction': 'input',
     'documented_range': '00H_to_FEH_first_two_digits_of_the_three_digit_io_number',
     'set_by': 'user'},
    {'name': 'n1', 'role': 'connection_number', 'direction': 'input',
     'documented_range': {'minimum': 1, 'maximum': 16}, 'set_by': 'user'},
    {'name': 'n2', 'role': 'number_of_consecutive_protocol_executions', 'direction': 'input',
     'documented_range': {'minimum': 1, 'maximum': 8}, 'set_by': 'user'},
    {'name': 'S', 'role': 'start_device_that_stores_control_data', 'direction': 'in_out',
     'set_by': 'user_and_system'},
    {'name': 'D', 'role': 'completion_device', 'direction': 'output', 'set_by': 'system',
     'bit_note': 'the_next_device_turns_on_when_the_instruction_ends_abnormally'},
)

GP_CONTROL_DATA = {
    '0': {'name': 'execution_count_result', 'set_by': 'system',
          'documented_range': {'minimum': 0, 'maximum': 8},
          'note': 'zero_when_the_setting_data_or_control_data_contains_an_error'},
    '1': {'name': 'completion_status', 'set_by': 'system',
          'note': 'zero_means_normal_completion_other_values_are_error_codes'},
    '2_to_9': {'name': 'execution_protocol_number_specification', 'set_by': 'user',
               'first_range': {'minimum': 1, 'maximum': 128},
               'following_range': {'minimum': 0, 'maximum': 128}},
    '10_to_17': {'name': 'matched_receive_packet_number', 'set_by': 'system',
                 'documented_range': {'minimum': 0, 'maximum': 16},
                 'note': 'zero_for_send_only_or_an_errored_protocol'},
}

ERROR_CODES = (
    ('C404H', 'protocol_cancel_request_error'),
    ('C407H', 'connection_number_setting_error'),
    ('C430H', 'protocol_setting_data_writing_during_instruction_execution'),
)

CAUTIONS = (
    'fixed_buffer_communication_must_be_set_to_predefined_protocol',
    'mc_protocol_and_fixed_buffer_and_random_access_communications_are_not_available_on_that_connection',
    'do_not_change_the_cpu_module_device_used_by_the_protocol_until_completion',
    'protocol_setting_data_writing_cancels_the_execution',
    'waiting_time_zero_waits_infinitely_for_the_protocol_data',
    'error_in_one_protocol_stops_the_following_protocols',
)

# Wording used to re-verify every published value against the indexed pages.
BASE_WITNESS = {
    'instruction_description': (
        'This instruction executes the protocols registered in the flash ROM'),
    'setting_data': (
        'Start I/O number of the E71',
        'Connection number (1 to 16)',
        'Number of consecutive protocol executions (1 to 8)',
        'Start number of the device that stores control data',
        'turns on for one scan upon completion of the instruction'),
    'control_data': (
        'Execution count result',
        'Completion status',
        'Execution protocol number specification 1',
        'Matched receive packet No.1'),
    'functions': (
        'The connection specified by n1 is used',
        'Protocols are executed consecutively for the number of times specified by n2',
        'the following instruction will be ignored until the first instruction is completed',
        'execution status check area'),
    'completion': (
        'Completion device',
        'Turns on at the END process of the scan where the ECPRTCL instruction is'),
    'errors': (
        'C404',
        'C407',
        'C430'),
    'caution': (
        'Predefined protocol',
        'not executed and the dedicated instruction ends abnormally',
        'wait infinitely'),
    'cancellation': (
        'cancel request',
        'Cancellation request',
        'Cancel completed'),
}

SP_WITNESS = {
    'deferral': (
        'the same as that of the GP',
        'Usage of the SP'),
    'differences': (
        'U0 (dummy) needs to be specified',
        'this is a dummy',
        'Open Setting',
        'Start Device to Store Predefined Protocol',
        'the pairing open setting is not available in the CPU module',
        'offset+18'),
    'start_device': (
        'start device',
        '19 words',
        'Protocol cancellation specification'),
}

# One record per documented source chain: CPU manual that defines SP.ECPRTCL and
# the module manual it defers to.
CHAINS = (
    {
        'id': 'qnu',
        'models': ('Q03UDV', 'Q04UDV', 'Q06UDV', 'Q13UDV', 'Q26UDV', 'Q03UDE', 'Q04UDEH',
                   'Q06UDEH', 'Q10UDEH', 'Q13UDEH', 'Q20UDEH', 'Q26UDEH', 'Q50UDEH',
                   'Q100UDEH', 'Q04UDPV', 'Q06UDPV', 'Q13UDPV', 'Q26UDPV'),
        'sp_manual': 'sh080811engy',
        'sp_number': 'SH080811ENG',
        'sp_revision': 'Y',
        'sp_title': 'QnUCPU User\'s Manual (Communication via Built-in Ethernet Port)',
        'sp_difference_pages': (87,),
        'sp_start_device_pages': (83, 85),
        'base_manual': 'sh080009y',
        'base_number': 'SH080009ENG',
        'base_revision': 'Y',
        'base_title': "Q Corresponding Ethernet Interface Module User's Manual (Basic)",
        'base_pages': (252, 253, 255, 256, 257, 258, 259),
        'base_section': '15.7 GP.ECPRTCL',
        'sp_difference_section': '6.4.1 Executing the predefined protocol (SP.ECPRTCL)',
        'sp_start_device_section': '6.2 Setting Method',
        'cancellation_source': 'module_buffer_memory_54D3H',
    },
    {
        'id': 'l',
        'models': ('L02', 'L02-P', 'L06', 'L06-P', 'L26', 'L26-BT', 'L26-P', 'L26-PBT'),
        'sp_manual': 'sh080891engs',
        'sp_number': 'SH080891ENG',
        'sp_revision': 'S',
        'sp_title': 'MELSEC-L CPU Module User\'s Manual (Built-In Ethernet Function)',
        'sp_difference_pages': (63,),
        'sp_start_device_pages': (59, 61),
        'base_manual': 'sh081105engg',
        'base_number': 'SH081105ENG',
        'base_revision': 'G',
        'base_title': "MELSEC-L Ethernet Interface Module User's Manual (Basic)",
        'base_pages': (208, 209, 211, 212, 213, 214),
        'base_section': '15.7 GP.ECPRTCL',
        'sp_difference_section': '6.4.1 Executing the predefined protocol (SP.ECPRTCL)',
        'sp_start_device_section': '6.2 Setting Method',
        'cancellation_source': 'module_buffer_memory_54D3H',
    },
)


def _plain(value):
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


def _squashed(value):
    """Case-folded alphanumerics only, so PDF glyph splits cannot hide a phrase."""
    return re.sub(r'[^a-z0-9]', '', (value or '').casefold())


def _contains(text, phrase):
    if _normalized(phrase) in _normalized(text):
        return True
    return _squashed(phrase) in _squashed(text)


def _verify(text, phrases, label):
    missing = [phrase for phrase in phrases if not _contains(text, phrase)]
    if missing:
        raise ValueError('ECPRTCL supplement source changed: %s is missing documented text: %s'
                         % (label, '; '.join(missing)))


def _manual_meta(source, manual_id, number, revision):
    rows = _rows(source, 'SELECT manual_id, manual_number, revision, title, official_url '
                         'FROM manuals WHERE manual_id=?', (manual_id,))
    if not rows:
        raise ValueError('ECPRTCL supplement source is missing manual metadata: ' + manual_id)
    meta = rows[0]
    if (meta['manual_number'] or '').replace('-', '').upper() != number.replace('-', '').upper():
        raise ValueError('ECPRTCL supplement source has an unexpected manual number: %s'
                         % meta['manual_number'])
    if (meta['revision'] or '').upper() != revision.upper():
        raise ValueError('ECPRTCL supplement source has an unexpected revision: %s'
                         % meta['revision'])
    return meta


def _covering_chunks(source, manual_id, page):
    return _rows(source, 'SELECT chunk_uid, manual_id, manual_number, revision, pdf_page, '
                         'pdf_page_end FROM chunks WHERE manual_id=? AND pdf_page<=? AND '
                         'pdf_page_end>=? ORDER BY id', (manual_id, page, page))


def _page_text(source, manual_id, page):
    rows = _rows(source, 'SELECT pdf_page, section, clean_text AS text FROM page_artifacts '
                         'WHERE manual_id=? AND pdf_page=?', (manual_id, page))
    if not rows:
        raise ValueError('ECPRTCL supplement source has no page artifact: %s p%s'
                         % (manual_id, page))
    return rows[0]


def _references(source, manual_id, pages, number, revision):
    """Cite the covering chunk when the page is indexed, otherwise the artifact."""
    references = []
    uncovered = []
    for page in pages:
        chunks = _covering_chunks(source, manual_id, page)
        if chunks:
            for chunk in chunks:
                references.append({'id': chunk['chunk_uid'], 'source_kind': 'chunk',
                                   'manual_id': chunk['manual_id'],
                                   'manual_number': chunk['manual_number'],
                                   'revision': chunk['revision'],
                                   'pdf_pages': list(range(chunk['pdf_page'],
                                                           chunk['pdf_page_end'] + 1))})
        else:
            uncovered.append(page)
            references.append({'id': 'artifact:%s:p%s' % (manual_id, page),
                               'source_kind': 'page_artifact', 'manual_id': manual_id,
                               'manual_number': number, 'revision': revision,
                               'pdf_pages': [page]})
    return references, uncovered


def _title_token(title):
    '''A typed-safe token for a documented manual title.'''
    token = re.sub(r'[^A-Za-z0-9]+', '_', title or '').strip('_').lower()
    return re.sub(r'_+', '_', token)


def _assert_sections(source, manual_id, pages, allowed, label):
    """Fail closed when a cited page belongs to another instruction section."""
    seen = {}
    for page in pages:
        row = _page_text(source, manual_id, page)
        seen[page] = row['section']
        if row['section'] not in allowed:
            raise ValueError('ECPRTCL supplement page crosses an instruction boundary: '
                             '%s p%s belongs to %s, expected one of %s'
                             % (manual_id, page, row['section'], sorted(allowed)))
    return seen


def _pages_text(source, manual_id, pages):
    return ' '.join(_page_text(source, manual_id, page)['text'] or '' for page in pages)


def _fact(identity, dimension, value, references, models, *, languages=None, note=None):
    scope = {'models': list(models), 'forms': [OPCODE],
             'languages': list(languages or ('ladder',))}
    fact = {'id': identity, 'dimension': dimension, 'value': _plain(value),
            'status': 'source_verified', 'verification': 'source_checked',
            'sources': [dict(reference) for reference in references], 'scope': scope}
    if note:
        fact['note'] = note
    return fact


def _chain_facts(chain, base_references, sp_references, combined_references):
    models = chain['models']
    facts = [
        _fact('ecprtcl:form@%s' % chain['id'], 'identity.form',
              {'mnemonic': OPCODE,
               'base_instruction': BASE_OPCODE,
               'instruction_family': 'predefined_protocol_execution',
               'callable_kind': 'dedicated_instruction',
               'documented_section': '6.4.1',
               'documented_forms': [OPCODE],
               'documented_base_forms': [BASE_OPCODE],
               'cpu_module_only': False,
               'axis_grouped': False,
               'documented_connection_range': {'minimum': 1, 'maximum': 16},
               'documented_models': list(models),
               'supplement_of': 'ecprtcl_call_parameters_deferred_to_the_module_manual'},
              combined_references, models),
        _fact('ecprtcl:languages@%s' % chain['id'], 'identity.language_availability',
              {'documented_languages': ['ladder'],
               'availability': {'ladder': True},
               'undocumented_languages': ['st', 'fbd'],
               'documented_as': 'ladder_diagram_form_and_differences_table_only',
               'undocumented_language_note':
                   'this_manual_documents_no_st_or_fbd_form_and_states_no_lack_of_support'},
              sp_references, models, languages=['ladder']),
        _fact('ecprtcl:cross_manual@%s' % chain['id'], 'identity.cross_manual_reference',
              {'deferred_from_manual_number': chain['sp_number'],
               'deferred_from_revision': chain['sp_revision'],
               'deferred_from_section': '6.4.1',
               'cited_manual_number': chain['base_number'],
               'cited_manual_revision': chain['base_revision'],
               'cited_manual_title_token': _title_token(chain['base_title']),
               'cited_section_token': _title_token(chain['base_section']),
               'deferral': 'sp_usage_is_the_same_as_gp_except_the_documented_differences',
               'previously_indexed': False},
              combined_references, models),
        _fact('ecprtcl:call_signature@%s' % chain['id'], 'parameters.call_signature',
              {'base_documented_call': 'GP.ECPRTCL_Un_n1_n2_(S)_(D)',
               'base_argument_order': ['Un', 'n1', 'n2', 'S', 'D'],
               'sp_documented_call': 'SP.ECPRTCL_U0_n1_n2_(S)_(D)',
               'sp_argument_order': ['U0', 'n1', 'n2', 'S', 'D'],
               'sp_form_basis': 'same_as_the_base_instruction_except_the_documented_differences',
               'sp_un_override': 'U0_dummy',
               'setting_data': list(GP_SETTING_DATA),
               'device_restriction':
                   'file_registers_set_for_each_local_device_and_program_cannot_be_used'},
              combined_references, models),
        _fact('ecprtcl:differences@%s' % chain['id'], 'parameters.documented_differences',
              {'base_instruction': BASE_OPCODE,
               'cancellation_source': chain['cancellation_source'],
               'sp_cancellation_source': 'start_device_offset_plus_18',
               'differences': list(DIFFERENCES)},
              sp_references, models),
        _fact('ecprtcl:start_device@%s' % chain['id'], 'parameters.start_device_layout',
              dict(SP_START_DEVICE, manual_number=chain['sp_number'],
                   documented_pages=list(chain['sp_start_device_pages'])),
              sp_references, models),
        _fact('ecprtcl:control_data@%s' % chain['id'], 'parameters.control_data_layout',
              {'base_control_data': dict(GP_CONTROL_DATA),
               'base_control_data_documented_for': BASE_OPCODE,
               'sp_status_source': 'start_device_to_store_predefined_protocol',
               'sp_status_offsets': {'execution_status': 0, 'execution_count': 17,
                                     'cancellation': 18},
               'do_not_copy': 'module_buffer_memory_offsets_into_the_sp_form'},
              base_references, models),
        _fact('ecprtcl:execution@%s' % chain['id'], 'execution.timing',
              {'consecutive_protocols_per_instruction': {'minimum': 1, 'maximum': 8},
               'completion_device_offsets': ['D', 'D+1'],
               'completion_device_rule':
                   'turns_on_at_the_end_process_of_the_completion_scan_and_off_at_the_next',
               'concurrent_same_connection': 'later_instruction_ignored_until_the_first_completes',
               'waiting_time_zero': 'waits_infinitely_for_the_protocol_data',
               'protocol_execution_status_source': 'start_device_offset_0'},
              base_references, models),
        _fact('ecprtcl:errors@%s' % chain['id'], 'constraints.operation_error',
              {'completion_status_device': 'S_plus_1',
               'codes': {code: name for code, name in ERROR_CODES},
               'multi_protocol_rule': 'an_error_stops_the_following_protocols',
               'cancel_result': 'the_instruction_ends_abnormally_with_the_cancel_error'},
              base_references, models),
        _fact('ecprtcl:caution@%s' % chain['id'], 'constraints.documented_caution',
              {'cautions': list(CAUTIONS),
               'fixed_buffer_requirement': 'predefined_protocol',
               'documented_as': 'precautions_and_functions_notes'},
              base_references, models),
        _fact('ecprtcl:effects@%s' % chain['id'], 'effects.result_rule',
              {'effect': 'executes_the_protocol_setting_data_registered_in_the_module',
               'sends_and_receives_packets': True,
               'execution_count_stored_in': 'control_data_plus_0',
               'matched_packet_number_stored_in': 'control_data_plus_10_to_plus_17',
               'behavior_keys_published': 0},
              base_references, models),
    ]
    for fact in facts:
        if fact['id'] not in {'ecprtcl:cross_manual@%s' % chain['id'],
                             'ecprtcl:differences@%s' % chain['id']}:
            fact['depends_on'] = ['ecprtcl:cross_manual@%s' % chain['id'],
                                  'ecprtcl:differences@%s' % chain['id']]
    return facts


def reviewed_ecprtcl_supplement(source, entries=None):
    """Return typed SP.ECPRTCL call-parameter facts keyed by (target_model, opcode)."""
    facts = {}
    chains_receipt = []
    for chain in CHAINS:
        sp_meta = _manual_meta(source, chain['sp_manual'], chain['sp_number'],
                               chain['sp_revision'])
        base_meta = _manual_meta(source, chain['base_manual'], chain['base_number'],
                                 chain['base_revision'])

        sp_difference_sections = _assert_sections(
            source, chain['sp_manual'], chain['sp_difference_pages'],
            {chain['sp_difference_section']}, chain['sp_manual'] + ' difference')
        sp_start_sections = _assert_sections(
            source, chain['sp_manual'], chain['sp_start_device_pages'],
            {chain['sp_start_device_section']}, chain['sp_manual'] + ' start device')
        base_sections = _assert_sections(
            source, chain['base_manual'], chain['base_pages'],
            {chain['base_section']}, chain['base_manual'] + ' instruction')
        sp_difference_text = _pages_text(source, chain['sp_manual'],
                                         chain['sp_difference_pages'])
        _verify(sp_difference_text, SP_WITNESS['deferral'], chain['sp_manual'] + ' deferral')
        _verify(sp_difference_text, SP_WITNESS['differences'],
                chain['sp_manual'] + ' differences')
        sp_start_text = _pages_text(source, chain['sp_manual'], chain['sp_start_device_pages'])
        _verify(sp_start_text, SP_WITNESS['start_device'],
                chain['sp_manual'] + ' start device')

        base_text = _pages_text(source, chain['base_manual'], chain['base_pages'])
        _verify(base_text, BASE_WITNESS['instruction_description'],
                chain['base_manual'] + ' description')
        _verify(base_text, BASE_WITNESS['setting_data'], chain['base_manual'] + ' setting data')
        _verify(base_text, BASE_WITNESS['control_data'], chain['base_manual'] + ' control data')
        _verify(base_text, BASE_WITNESS['functions'], chain['base_manual'] + ' functions')
        _verify(base_text, BASE_WITNESS['completion'], chain['base_manual'] + ' completion')
        _verify(base_text, BASE_WITNESS['errors'], chain['base_manual'] + ' errors')
        _verify(base_text, BASE_WITNESS['caution'], chain['base_manual'] + ' caution')
        _verify(base_text, BASE_WITNESS['cancellation'],
                chain['base_manual'] + ' cancellation')

        sp_references, sp_uncovered = _references(
            source, chain['sp_manual'],
            tuple(chain['sp_difference_pages']) + tuple(chain['sp_start_device_pages']),
            chain['sp_number'], chain['sp_revision'])
        base_references, base_uncovered = _references(
            source, chain['base_manual'], chain['base_pages'], chain['base_number'],
            chain['base_revision'])
        combined = list(base_references)
        for reference in sp_references:
            combined.append(reference)

        chain_facts = _chain_facts(chain, base_references, sp_references, combined)
        for model in chain['models']:
            model_facts = []
            for fact in chain_facts:
                clone = dict(fact)
                clone['scope'] = dict(fact['scope'], models=[model])
                model_facts.append(clone)
            facts[(model, OPCODE)] = model_facts

        chains_receipt.append({
            'chain': chain['id'],
            'models': list(chain['models']),
            'sp_manual': {'manual_id': chain['sp_manual'],
                          'manual_number': sp_meta['manual_number'],
                          'revision': sp_meta['revision'],
                          'title': sp_meta['title'],
                          'official_url': sp_meta['official_url'],
                          'documented_pages': sorted(set(
                              tuple(chain['sp_difference_pages']) +
                              tuple(chain['sp_start_device_pages']))),
                          'pages_without_chunks': sp_uncovered},
            'base_manual': {'manual_id': chain['base_manual'],
                            'manual_number': base_meta['manual_number'],
                            'revision': base_meta['revision'],
                            'title': base_meta['title'],
                            'official_url': base_meta['official_url'],
                            'documented_section': chain['base_section'],
                            'documented_pages': list(chain['base_pages']),
                            'pages_without_chunks': base_uncovered},
            'section_binding': {
                'rule': 'every_cited_page_must_carry_the_documented_section',
                'sp_difference_sections': sp_difference_sections,
                'sp_start_device_sections': sp_start_sections,
                'base_sections': base_sections,
            },
            'recovered_topics': ['call_signature', 'setting_data_ranges',
                                 'control_data_layout', 'completion_device_timing',
                                 'operation_error_codes', 'cancellation_procedure',
                                 'concurrency_and_waiting_time_rules'],
        })

    receipt = {
        'method': METHOD,
        'opcode': OPCODE,
        'base_opcode': BASE_OPCODE,
        'counts': {'facts': sum(len(rows) for rows in facts.values()),
                   'owner_keys': len(facts),
                   'models': sorted({model for model, _opcode in facts}),
                   'by_dimension': _count_by(facts, 'dimension'),
                   'by_status': _count_by(facts, 'status')},
        'chains': chains_receipt,
        'gap': {'instructions_table_had_base_opcode': False,
                'module_manuals_were_registered': False,
                'module_pdfs_already_on_disk': True,
                'download_required': False},
        'guarantees': {'database_mutated': False, 'pdf_read_at_runtime': False,
                       'network_at_runtime': False, 'manual_prose_published': False,
                       'executable_behavior_keys': 0, 'call_available_promotions': 0,
                       'cpu_widening': 0,
                       'module_un_copied_into_the_sp_form': 0},
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

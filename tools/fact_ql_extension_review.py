"""Source-bound knowledge for Q/L Ethernet, built-in I/O and data logging.

The native mnemonic, parameter order and documented operation are independent
of a structured-language wrapper or a Core executor.  No rule in this module
grants either capability.  Review locators include continuation/precaution pages
because the private instruction catalogue sometimes indexes only the first page.
"""
from __future__ import annotations

import copy
import json
import re
from collections import Counter

METHOD = 'authored_ql_extension_review_v2'
MANUALS = ('sh080811engy', 'sh080891engs', 'sh080892engj', 'sh080893engu')


def operand(name, role, direction='input', type='WORD_DEVICE', **extra):
    return dict(name=name, role=role, direction=direction, type=type, **extra)


U0 = operand('U0', 'dummy', type='STRING_LITERAL', fixed='U0')
CONTROL = lambda name: operand(name, 'control_data_head', 'in_out')
DONE = lambda name: operand(name, 'completion_pair_head', 'output', 'BIT_DEVICE', extent=2)
CONNECTION = operand('S1', 'connection_number', type='BIN16', min=1, max=16)

# Every operation is written against the instruction's own setting table and
# function paragraphs. These are formal values, never extracted manual prose.
NETWORK = {
    'SP.SLMPSND': dict(pages=(69, 78, 46, 54),
        params=[U0, CONTROL('S1'), operand('S2', 'request_frame'),
                operand('D1', 'response_frame', 'output'), DONE('D2')],
        operation='send_SLMP_request', object='S2_request_frame', result='D1_response_frame',
        control={'S1+0': {'arrival_confirmation': [0, 1], 'error_clock_recording': [0, 1]},
                 'S1+2': {'channel': [1, 9], 'serial_number_absent_channel': 1},
                 'S1+3..4': 'target_IP', 'S1+5': {'target_port': [1, 65534]},
                 'S1+6': 0, 'S1+7': 255, 'S1+9': 0,
                 'S1+10': {'retries': [0, 15], 'direction': 'in_out'},
                 'S1+11': {'monitor_seconds': [1, 32767], 'zero_means_seconds': 10}},
        witness='arrival check', completion='D2'),
    'SP.ECPRTCL': dict(pages=(87, 87, 63, 63), params=None,
        operation='execute_configured_protocol', object='GX_Works2_protocol_packets',
        result='send_and_receive_packets', witness='sends and receives packets',
        control={'U0': 'dummy', 'n1': 'built_in_Ethernet_connection_number',
                 'cancel': 'predefined_protocol_start_device+18', 'pairing_open': False},
        conditions=[{'required_open_system': 'Predefined_Protocol'},
                    {'control_and_request_data': 'unchanged_until_completion'},
                    {'next_protocol_instruction': 'wait_for_completion'}],
        dependency={'kind': 'instruction', 'target': 'GP.ECPRTCL',
                    'relation': 'setting_table_with_documented_overrides'}),
    'SP.SOCOPEN': dict(pages=(110, 113, 88, 93),
        params=[U0, CONNECTION, CONTROL('S2'), DONE('D')],
        operation='open_connection', object='S1_connection', result='connection_established',
        witness='connection', completion='D',
        control={'S2+0': {'0': 'parameter_open_settings', '32768': 'control_data+2..9'},
                 'S2+1': 'completion_status', 'S2+2': 'protocol_and_open_mode',
                 'S2+3': 'host_port', 'S2+4..5': 'target_IP', 'S2+6': 'target_port',
                 'S2+7..9': 'system_area_use_prohibited'}),
    'SP.SOCCLOSE': dict(pages=(114, 116, 94, 96),
        params=[U0, CONNECTION, CONTROL('S2'), DONE('D')],
        operation='close_connection', object='S1_connection', result='connection_disconnected',
        witness='connection', completion='D',
        control={'S2+0': 'system_area', 'S2+1': 'completion_status'},
        state={'passive_open': 'open_request_and_completion_off_transfer_disabled'}),
    'SP.SOCRCV': dict(pages=(117, 120, 97, 100),
        params=[U0, CONNECTION, CONTROL('S2'), operand('D1', 'received_data', 'output'), DONE('D2')],
        operation='read_socket_receive_buffer', object='S1_connection_buffer',
        result='D1_length_and_received_bytes', witness='END processing', completion='D2',
        timing={'data_read': 'END_processing', 'scan_time': 'increased_by_read_processing'},
        control={'S2+1': 'completion_status', 'D1+0': 'received_byte_count', 'D1+1..': 'received_data'},
        state={'odd_byte_count': 'upper_byte_of_last_word_invalid'},
        conditions=[{'same_connection': 'do_not_mix_SP.SOCRCV_and_S.SOCRCVS'}]),
    'S.SOCRCVS': dict(pages=(121, 123, 101, 103),
        params=[operand('S', 'connection_number', type='BIN16', min=1, max=16),
                operand('D', 'received_data', 'output')],
        operation='read_socket_receive_buffer', object='S_connection_buffer',
        result='D_length_and_received_bytes', witness='instruction execution',
        timing={'data_read': 'instruction_execution'},
        control={'D+0': 'received_byte_count', 'D+1..': 'received_data'},
        state={'odd_byte_count': 'upper_byte_of_last_word_invalid'},
        conditions=[{'same_connection': 'do_not_mix_SP.SOCRCV_and_S.SOCRCVS'}]),
    'SP.SOCSND': dict(pages=(124, 127, 104, 107),
        params=[U0, CONNECTION, CONTROL('S2'), operand('S3', 'send_data'), DONE('D')],
        operation='send_socket_data', object='S3_bytes', result='S1_connection_transmission',
        witness='send', completion='D',
        control={'S2+1': 'completion_status', 'S3+0': 'send_byte_count', 'S3+1..': 'send_data'},
        conditions=[{'TCP_send_size': 'not_greater_than_target_TCP_window'}]),
    'SP.SOCCINF': dict(pages=(128, 130, 108, 110),
        params=[U0, CONNECTION, CONTROL('S2'), operand('D', 'connection_information', 'output')],
        operation='read_connection_information', object='S1_connection', result='D_connection_fields',
        witness='connection information',
        control={'S2+1': 'completion_status', 'D+0..1': 'target_IP', 'D+2': 'target_port',
                 'D+3': 'host_port', 'D+4': 'connection_application'},
        state={'unopened_connection': 'all_fields_zero',
               'broadcast_destination_after_receive': 'actual_sender_IP_and_port'}),
    'SP.SOCCSET': dict(pages=(131, 132, 111, 112),
        params=[U0, CONNECTION, CONTROL('S2')],
        operation='change_UDP_destination', object='S1_connection', result='new_target_without_close',
        witness='UDP',
        control={'S2+1': 'completion_status', 'S2+2..3': 'new_target_IP', 'S2+4': 'new_target_port'},
        timing={'receive_buffer_empty': 'change_after_instruction',
                'receive_buffer_not_empty': 'change_after_next_SP.SOCRCV_or_S.SOCRCVS'},
        conditions=[{'transport': 'UDP'}, {'SP.SOCSND_active': 'do_not_change_destination'}]),
    'SP.SOCRMODE': dict(pages=(133, 136, 113, 115),
        params=[U0, CONNECTION, CONTROL('S2')],
        operation='change_TCP_receive_mode', object='S1_connection', result='mode_and_receive_size_updated',
        witness='fixed-length receive mode',
        control={'S2+1': 'completion_status', 'S2+2': {'0': 'standard', '1': 'fixed_length'},
                 'S2+3': 'receive_byte_count'},
        state={'standard': 'receive_state_on_when_data_received',
               'fixed_length': 'receive_state_on_only_when_requested_size_reached',
               'excess_data': 'retained_for_next_read'},
        conditions=[{'transport': 'TCP', 'UDP': 'instruction_invalid'}]),
    'SP.READ': dict(pages=(211, 216, None, None),
        params=[U0, CONTROL('S1'), operand('S2', 'remote_start_device'),
                operand('D1', 'local_destination', 'output'), DONE('D2')],
        operation='read_remote_CPU_devices', object='S2_remote_devices', result='D1_local_words',
        witness='data to be read', completion='D2',
        control={'S1+1': 'completion_status', 'S1+2': {'channel': [1, 8]},
                 'S1+3': [0, 1023], 'S1+4..5': 'remote_IP', 'S1+6': 0,
                 'S1+7': {'retries': [0, 15], 'direction': 'in_out'},
                 'S1+8': {'monitor_1s': [1, 16383], 'monitor_100ms': [1, 65535]},
                 'S1+9': {'read_words': [1, 960]}},
        conditions=[{'remote_device_range': 'must_fit_host_same_device_family_range'},
                    {'local_devices_and_per_program_file_registers': 'not_allowed'}]),
    'SP.WRITE': dict(pages=(217, 223, None, None),
        params=[U0, CONTROL('S1'), operand('S2', 'local_source'),
                operand('D1', 'remote_destination', 'output'), DONE('D2')],
        operation='write_remote_CPU_devices', object='S2_local_words', result='D1_remote_words',
        witness='data to be written', completion='D2',
        control={'S1+0': {'arrival_confirmation': [0, 1]}, 'S1+1': 'completion_status',
                 'S1+2': {'channel': [1, 8]}, 'S1+3': [0, 1023], 'S1+4..5': 'remote_IP',
                 'S1+6': 0, 'S1+7': {'retries': [0, 15], 'direction': 'in_out'},
                 'S1+8': {'monitor_1s': [1, 16383], 'monitor_100ms': [1, 65535]},
                 'S1+9': {'write_words': [1, 960]}},
        timing={'arrival_confirmation_0': 'complete_after_host_send',
                'arrival_confirmation_1': 'complete_after_remote_write'},
        conditions=[{'remote_device_range': 'must_fit_host_same_device_family_range'},
                    {'local_devices_and_per_program_file_registers': 'not_allowed'}]),
}


for _op, _start, _end, _operation, _result in (
    ('SP.FTPPUT', 148, 153, 'upload_CPU_file', 'FTP_server_directory'),
    ('SP.FTPGET', 154, 158, 'download_FTP_file', 'CPU_directory'),
):
    NETWORK[_op] = dict(pages=(None, None, _start, _end),
        params=[U0, CONTROL('S1'), operand('S2', 'source_file_path', type='STRING_OR_DEVICE'),
                operand('S3', 'destination_directory', type='STRING_OR_DEVICE'), DONE('D')],
        operation=_operation, object='S2_file', result=_result, witness='FTP server', completion='D',
        control={'S1+0': {'overwrite': ['none', 'all', 'updated_only'], 'power_off_protection': [0, 1]},
                 'S1+1': 'completion_status', 'S1+2': 'total_files', 'S1+3': 'completed_files'},
        state={'connection': 'open_from_FTP_Client_Setting_and_release_after_transfer'},
        conditions=[{'FTPPUT_and_FTPGET': 'mutually_exclusive_later_request_ignored'},
                    {'path_literal_max_bytes': 32, 'path_device_max_bytes': 255}])

NETWORK.update({
    'SP.MLSEND': dict(pages=(None, None, 213, 216),
        params=[U0, CONTROL('S1'), operand('S2', 'subject_and_body'), DONE('D')],
        operation='send_email', object='S2_subject_and_body', result='selected_destination_mail',
        witness='Sends e-mail', completion='D',
        control={'S1+0': {'address_source': ['PLC_parameter', 'CPU_memory'], 'format': 'binary'},
                 'S1+1': 'completion_status', 'S1+2': {'destination': [1, 16]},
                 'S1+9': {'total_words': [1, 4469]}, 'S1+10': {'subject_words': [0, 373]}},
        conditions=[{'message_body': 'ASCII', 'line_max_characters': 78,
                     'line_terminator': 'CRLF', 'body_terminator': 'NULL', 'one_byte_katakana': False}]),
    'SP.MLRECV': dict(pages=(None, None, 217, 220),
        params=[U0, CONTROL('S'), operand('D1', 'received_header_and_body', 'output'), DONE('D2')],
        operation='read_and_delete_server_email', object='selected_server_message', result='D1_header_and_body',
        witness='deleted from the mail server', completion='D2',
        control={'S+0': {'query_after_read': [0, 1], 'receive_type': 'message_body_only'},
                 'S+1': 'completion_status', 'S+2': {'message_index': [0, 65535], 'zero': 'first'},
                 'S+9': {'capacity_words': [0, 4469], 'zero': 'received_length', 'output': 'actual_words'},
                 'S+10': {'header_words': [0, 373], 'zero': 'received_header_length'}},
        state={'message_exceeds_capacity': 'remainder_discarded',
               'odd_header_length': 'last_word_upper_byte_NULL',
               'MC_command_mail': 'SM1659_on_until_reply_END_processing'}),
    'SP.MLOPEADR': dict(pages=(None, None, 221, 223),
        params=[U0, CONTROL('S1'), operand('S2', 'destination_slot', type='BIN16', min=1, max=16),
                operand('S3', 'email_address', type='STRING_OR_DEVICE'), DONE('D')],
        operation='register_or_delete_email_address', object='S3_address', result='CPU_address_table_S2_slot',
        witness='registers/deletes', completion='D',
        control={'S1+0': {'operation': {'0': 'register', '1': 'delete'},
                         'backup': {'0': 'enabled', '1': 'disabled'}}, 'S1+1': 'completion_status'},
        state={'duplicate_register': 'duplicate_addresses_and_duplicate_delivery',
               'backup': 'flash_ROM_restore_at_initialization', 'backup_in_progress': 'SM1661'},
        conditions=[{'address_characters': [32, 126], 'excluded_character': 44,
                     'slot_total_max_characters': 500, 'one_address_per_call': True},
                    {'SM1661_on': 'no_reset_or_power_off'}, {'table': 'not_PLC_parameter_table'}]),
    'SP.MLGETADR': dict(pages=(None, None, 224, 225),
        params=[U0, CONTROL('S1'), operand('S2', 'destination_slot', type='BIN16', min=1, max=16),
                operand('D1', 'email_address_string', 'output'), DONE('D2')],
        operation='read_email_address_table', object='S2_destination_slot', result='D1_NULL_terminated_addresses',
        witness='e-mail address', completion='D2',
        control={'S1+0': {'0': 'PLC_parameter', '1': 'CPU_memory'}, 'S1+1': 'completion_status',
                 'S1+2': {'capacity': [1, 501], 'output': 'address_length_plus_NULL'}},
        state={'empty_slot': 'NULL', 'multiple_addresses': 'comma_separated'}),
})

# (family, pages, forms, operands, documented operation, result). A subheading
# owns all its literal forms; it does not inherit all of a multi-instruction page.
IO = [
    ('IPPSTRT', (140, 141), 'IPPSTRT1 IPPSTRT1P IPPSTRT2 IPPSTRT2P',
     [operand('n', 'positioning_data_number', type='BIN16', min=1, max=10)],
     'start_positioning_table', 'selected_axis_uses_preconfigured_data_n'),
    ('IPDSTRT', (142, 144), 'IPDSTRT1 IPDSTRT1P IPDSTRT2 IPDSTRT2P', [operand('S', 'control_data')],
     'start_positioning_from_devices', 'selected_axis_uses_S_to_S+7'),
    ('IPSIMUL', (145, 147), 'IPSIMUL IPSIMULP',
     [operand('n1', 'axis1_data_number', type='BIN16', min=1, max=10),
      operand('n2', 'axis2_data_number', type='BIN16', min=1, max=10)],
     'start_two_axes_simultaneously', 'axis1_n1_and_axis2_n2'),
    ('IPOPR', (148, 150), 'IPOPR1 IPOPR1P IPOPR2 IPOPR2P', [operand('S', 'OPR_control_data')],
     'start_origin_return', 'selected_axis_machine_or_fast_OPR'),
    ('IPJOG', (151, 153), 'IPJOG1 IPJOG2',
     [operand('S1', 'JOG_control_data'), operand('S2', 'direction', type='BIT', min=0, max=1)],
     'run_JOG', 'selected_axis_speed_from_S1_direction_from_S2'),
    ('IPABRST', (154, 155), 'IPABRST1 IPABRST2',
     [operand('S', 'servo_input_bits', type='BIT_DEVICE', extent=3),
      operand('D', 'servo_output_bits', 'output', 'BIT_DEVICE', extent=3)],
     'restore_absolute_position', 'servo_retained_position_to_current_feed_value'),
    ('IPSTOP', (156, 157), 'IPSTOP1 IPSTOP2', [],
     'decelerate_axis_stop', 'axis_stopped_without_positioning_completion'),
    ('IPSPCHG', (158, 160), 'IPSPCHG1 IPSPCHG1P IPSPCHG2 IPSPCHG2P', [operand('S', 'speed_change_data')],
     'change_axis_speed', 'new_speed_and_acceleration_from_S_to_S+3'),
    ('IPTPCHG', (161, 162), 'IPTPCHG1 IPTPCHG1P IPTPCHG2 IPTPCHG2P',
     [operand('S', 'new_target_position', type='BIN32_OR_DEVICE')],
     'change_axis_target', 'new_absolute_target_position'),
    ('ICCNTRD', (244, 244), 'ICCNTRD1 ICCNTRD1P ICCNTRD2 ICCNTRD2P', [],
     'read_current_counter', 'channel_current_value_special_register_pair'),
    ('ICRNGWR', (245, 246), 'ICRNGWR1 ICRNGWR1P ICRNGWR2 ICRNGWR2P',
     [operand('S1', 'ring_lower_limit', type='BIN32_OR_DEVICE'),
      operand('S2', 'ring_upper_limit', type='BIN32_OR_DEVICE')],
     'set_ring_counter_limits', 'channel_lower_and_upper_limit'),
    ('ICPREWR', (247, 247), 'ICPREWR1 ICPREWR1P ICPREWR2 ICPREWR2P',
     [operand('S', 'preset_value', type='BIN32_OR_DEVICE')],
     'set_counter_preset', 'channel_preset_value'),
    ('ICLTHRD', (248, 249), 'ICLTHRD1 ICLTHRD1P ICLTHRD2 ICLTHRD2P',
     [operand('n', 'latch_number', type='BIN16', min=1, max=2), operand('D', 'latched_count', 'output', extent=2)],
     'read_latched_counter', 'D_and_D+1_latch_n_value'),
    ('ICSMPRD', (250, 251), 'ICSMPRD1 ICSMPRD1P ICSMPRD2 ICSMPRD2P',
     [operand('D', 'sampling_count', 'output', extent=2)],
     'read_sampling_counter', 'D_and_D+1_sampling_value'),
    ('ICCOVWR', (252, 253), 'ICCOVWR1 ICCOVWR1P ICCOVWR2 ICCOVWR2P',
     [operand('n', 'coincidence_point_number', type='BIN16', min=1, max=2),
      operand('S', 'coincidence_value', type='BIN32_OR_DEVICE')],
     'set_counter_coincidence', 'channel_coincidence_n_value'),
    ('ICFCNT', (254, 254), 'ICFCNT1 ICFCNT2', [operand('D', 'measured_frequency', 'output', extent=2)],
     'measure_frequency', 'D_and_D+1_frequency_for_configured_unit_time'),
    ('ICRCNT', (255, 255), 'ICRCNT1 ICRCNT2', [operand('D', 'measured_rotation_speed', 'output', extent=2)],
     'measure_rotation_speed', 'D_and_D+1_speed_for_configured_unit_time'),
    ('ICPLSRD', (256, 256), 'ICPLSRD1 ICPLSRD1P ICPLSRD2 ICPLSRD2P',
     [operand('D', 'measured_pulse', 'output', extent=2)],
     'read_measured_pulse', 'D_and_D+1_pulse_value'),
    ('ICPWM', (257, 258), 'ICPWM1 ICPWM2',
     [operand('S1', 'PWM_on_width', type='BIN32_OR_DEVICE'), operand('S2', 'PWM_cycle', type='BIN32_OR_DEVICE')],
     'output_PWM', 'channel_coincidence_output1_waveform'),
]


def _pages(source, manual, start, end):
    rows = source.execute('SELECT id,manual_id,manual_number,revision,pdf_page,pdf_page_end,text '
                          'FROM chunks WHERE manual_id=? AND pdf_page<=? AND '
                          'coalesce(pdf_page_end,pdf_page)>=? ORDER BY pdf_page,id',
                          (manual, end, start)).fetchall()
    names = ('id', 'manual_id', 'manual_number', 'revision', 'pdf_page', 'pdf_page_end', 'text')
    rows = [dict(zip(names, tuple(row))) for row in rows]
    text = []
    for row in rows:
        for match in re.finditer(r'\[PAGE (\d+) PROSE\]\n(.*?)(?=\n\[PAGE \d+ (?:PROSE|LAYOUT)\]|\Z)', row['text'], re.S):
            if start <= int(match[1]) <= end:
                text.append(match[2])
    return rows, re.sub(r'\s+', ' ', '\n'.join(text))


def _source_refs(rows, pages, symbol):
    return [dict(id=str(row['id']), manual_id=row['manual_id'], manual_number=row['manual_number'],
                 revision=row['revision'], pdf_pages=list(range(max(pages[0], row['pdf_page']),
                    min(pages[1], row['pdf_page_end'] or row['pdf_page']) + 1)), opcode=symbol)
            for row in rows]


def _fact(manual, symbol, dimension, value, rows, pages, model, conditions=(), suffix=''):
    return dict(id='authored:ql_extension:' + manual + ':' + dimension + suffix,
        dimension=dimension, value=copy.deepcopy(value), status='source_verified', verification='source_checked',
        scope={'models': [model], 'forms': [symbol], 'languages': ['ladder']},
        sources=_source_refs(rows, pages, symbol), **({'conditions': copy.deepcopy(list(conditions))} if conditions else {}))


def reviewed_extension_behavior(source, entries=()):
    """Compile reviewed manual knowledge; report every unresolved source binding."""
    manuals = {}
    for mid in MANUALS:
        row = source.execute('SELECT plc_models FROM manuals WHERE manual_id=?', (mid,)).fetchone()
        if row:
            manuals[mid] = [item.strip() for item in re.split('[,|]', row[0]) if item.strip()]
    facts, inventory, gaps = {}, [], []

    def emit(mid, symbols, pages, dimensions, conditions=(), extra_rows=(), extra_pages=None):
        rows, text = _pages(source, mid, *pages)
        if not rows or not text:
            gaps.append(dict(manual=mid, pages=pages, symbols=list(symbols), reason='source_page_missing'))
            return
        for symbol in symbols:
            for model in manuals.get(mid, ()):
                target = facts.setdefault((model, symbol), [])
                for dimension, value in dimensions.items():
                    target.append(_fact(mid, symbol, dimension, value, rows, pages, model, conditions))
                if extra_rows:
                    target.append(_fact(mid, symbol, 'constraints.execution_context', extra_rows,
                                        *_pages(source, mid, *extra_pages)[:1], extra_pages, model, conditions))
        inventory.append(dict(manual=mid, pages=list(pages), declared_symbols=list(symbols),
                              disposition='behaviour_extracted', dimensions=list(dimensions)))

    for symbol, record in NETWORK.items():
        for mid, offset in (('sh080811engy', 0), ('sh080891engs', 2)):
            pages = record['pages'][offset:offset + 2]
            if pages[0] is None:
                continue
            rows, text = _pages(source, mid, *pages)
            if record['witness'].casefold() not in text.casefold():
                gaps.append(dict(manual=mid, pages=list(pages), symbols=[symbol], reason='operation_witness_changed'))
                continue
            conditions = copy.deepcopy(record.get('conditions', []))
            # Serial-number limits are chapter conditions, not family defaults.
            for match in re.finditer(r'serial number.{0,65}?["“](\d{5})["”].{0,25}?or earlier', text, re.I):
                # The 12051 footnote limits transfer length, not availability.
                # Only an operation-error condition denies instruction use.
                if '4002' not in text[match.end():match.end() + 140]:
                    continue
                condition = {'cpu_serial_first_five': {'greater_than': int(match[1])}}
                if 'QnUDE(H)' in text and mid == 'sh080811engy':
                    condition['models'] = [m for m in manuals[mid] if 'UDE' in m]
                if condition not in conditions:
                    conditions.append(condition)
            dimensions = {'effects.documented_operation': {key: record[key] for key in ('operation', 'object', 'result')},
                          'effects.state_control_data': record.get('control', {}),
                          'execution.documented': record.get('timing', {'activation': 'instruction_command'})}
            if record.get('params') is not None:
                dimensions['parameters.typed_operands'] = {'operands': record['params']}
            else:
                dimensions['parameters.documented_dependency'] = record['dependency']
                gaps.append(dict(manual=mid, pages=list(pages), symbols=[symbol],
                                 reason='referenced_setting_table_required', dependency=record['dependency']))
            if record.get('state'):
                dimensions['effects.state_operation'] = record['state']
            if record.get('completion'):
                completion = {'device': record['completion'], 'error_device': record['completion'] + '+1',
                              'duration': 'one_scan', 'error_on': 'abnormal_completion'}
                if re.search(r'(?:next END|next\s+END)', text, re.I):
                    completion.update(set_at='completion_scan_END', clear_at='next_END')
                dimensions['effects.state_completion'] = completion
            if symbol in {'SP.SOCRCV', 'S.SOCRCVS', 'SP.SOCSND', 'SP.SOCRMODE'}:
                dimensions['constraints.transfer_size'] = {'maximum_bytes': 10238 if mid == 'sh080811engy' else 2046,
                    **({'default_receive_bytes': 2046} if symbol != 'SP.SOCSND' else {}),
                    **({'older_QnUDEH_serial_at_most_12051_bytes': 2046} if mid == 'sh080811engy' else {})}
            emit(mid, [symbol], pages, dimensions, conditions)

    for family, pages, forms, params, operation, result in IO:
        symbols = forms.split()
        rows, text = _pages(source, 'sh080892engj', *pages)
        # All forms are independently named on their own pages, including forms
        # lost from the original catalogue (ICRNGWR and ICRCNT).
        missing = [symbol for symbol in symbols if symbol not in text]
        if missing:
            gaps.append(dict(manual='sh080892engj', pages=list(pages), symbols=missing, reason='form_witness_changed'))
            continue
        positioning = family.startswith('IP')
        conditions = [{'hardware_function': 'positioning' if positioning else 'high_speed_counter', 'enabled': True}]
        dimensions = {'parameters.typed_operands': {'operands': params},
                      'effects.documented_operation': {'operation': operation, 'object': 'axis_or_channel_in_mnemonic', 'result': result}}
        if positioning:
            dimensions['constraints.positioning_enabled'] = {'required_setting': 'Use', 'error_if_disabled': 4116}
            dimensions['execution.documented'] = {'command': 'native_instruction_execution'}
            if family in {'IPSTOP', 'IPSPCHG', 'IPTPCHG'}:
                dimensions['effects.state_ignored_status'] = {'axis_status': [0, 1, -1, 7, 8, 11], 'effect': 'no_processing'}
            if family in {'IPPSTRT', 'IPDSTRT', 'IPSIMUL', 'IPOPR', 'IPABRST'}:
                dimensions['effects.state_positioning_completion'] = {'busy_on': 'successful_start', 'busy_off': 'completion',
                    'completion_on': 'normal_completion', 'completion_off': 'next_axis_start',
                    'completion_after_error': False}
            if family == 'IPABRST':
                dimensions['effects.state_absolute_restore'] = {'IPSTOP_during_restore': 'ignored',
                    'current_feed_value': 'servo_retained_position', 'axis1_registers': ['SD1840', 'SD1841']}
            if family == 'IPJOG':
                dimensions['effects.state_JOG'] = {'busy_on': 'start', 'busy_off': 'end', 'positioning_completion_on': False,
                                          'direction_0': 'forward', 'direction_1': 'reverse'}
            if family == 'IPDSTRT':
                dimensions['effects.state_control_data'] = {'S+0': {'1': 'ABS', '2': 'INC', '3': 'speed_position_forward',
                    '4': 'speed_position_reverse', '5': 'current_value_change', '6': 'speed_forward', '7': 'speed_reverse'},
                    'S+1': 'acc_dec_ms', 'S+2': 'stop_dec_ms', 'S+3': 'dwell_ms',
                    'S+4..5': 'speed_pulses_per_second', 'S+6..7': 'position_pulses'}
            if family == 'IPOPR':
                dimensions['effects.state_control_data'] = {'S+0': {'1': 'machine_OPR', '2': 'fast_OPR_origin', '3': 'fast_OPR_standby'},
                    'S+1..2': 'standby_address_valid_only_for_type3',
                    'machine_OPR': {'request_on': 'start', 'request_off': 'complete', 'OPR_complete_off': 'next_start'}}
            if family in {'IPJOG', 'IPSTOP'}:
                dimensions['execution.disabled'] = {'command_off': 'instruction_still_processed_errors_possible',
                    'program_requirement': 'must_observe_command_off', 'same_axis_multiple_per_scan': 'not_guaranteed'}
            if family == 'IPSTOP':
                dimensions['constraints.stop_command'] = {'hold_ms_greater_than': 2,
                    'positioning_start_while_on': 'rejected_axis_error_1102'}
            extra = {'same_axis_start_while_SM1848_on': 'ignored'} if family in {'IPPSTRT', 'IPDSTRT', 'IPSIMUL', 'IPOPR', 'IPJOG', 'IPABRST'} else {}
            extra_pages = (163, 163)
        else:
            mode = {'ICFCNT': 'frequency_measurement', 'ICRCNT': 'rotation_speed_measurement',
                    'ICPLSRD': 'pulse_measurement', 'ICPWM': 'PWM_output'}.get(family, 'normal')
            conditions.append({'operation_mode': mode})
            dimensions['execution.documented'] = {'command': 'native_instruction_execution'}
            if family in {'ICFCNT', 'ICRCNT'}:
                dimensions['execution.measurement'] = {'start': 'execution_command_rising', 'end': 'execution_command_falling'}
            if family == 'ICRNGWR':
                conditions.append({'counter_format': 'ring', 'S1_lower_lte_S2_upper': True})
            if family == 'ICLTHRD':
                conditions.append({'n1_function': ['latch_counter', 'latch_counter_preset'],
                                   'n2_external_input': 'X8_or_X9_latch_counter_signal'})
            if family == 'ICSMPRD':
                conditions.append({'counter_function': 'sampling_counter'})
            if family == 'ICPWM':
                dimensions['constraints.PWM'] = {'on_width_0.1us': {'zero_allowed': True, 'range': [10, 10000000]},
                    'cycle_0.1us': [50, 10000000], 'on_width_lte_cycle': True, 'initial_output': 'off'}
            extra = ({'command_off': 'instruction_still_processed_errors_possible',
                      'same_channel_multiple_per_scan': 'not_guaranteed',
                      'program_requirement': 'must_observe_command_off'}
                     if family in {'ICFCNT', 'ICRCNT', 'ICPWM'} else {})
            extra_pages = (259, 259)
        emit('sh080892engj', symbols, pages, dimensions, conditions, extra, extra_pages)

    for symbol, pages in (('SP.LOGNAMER', (175, 176)), ('SP.LOGFILES', (177, 179))):
        mid = 'sh080893engu'
        models = manuals.get(mid, [])
        manuals[mid] = [m for m in models if 'UDV' in m or 'UDPV' in m]
        conditions = [{'cpu_serial_first_five': {'at_least': 20073}}, {'configuration_tool_version_at_least': '1.84N'}]
        common = {'n': {'setting_number': [1, 10]}, 'version_reference': 'sh080893engu:page192'}
        if symbol == 'SP.LOGNAMER':
            dimensions = {'parameters.typed_operands': {'operands': [operand('n', 'setting_number', type='BIN16', min=1, max=10),
                operand('D', 'logging_name', 'output', extent=17)]},
                'effects.documented_operation': {'operation': 'read_logging_name', 'object': 'setting_n', 'result': 'D_to_D+16'},
                'effects.state_logging_name': {'encoding': 'Shift_JIS', 'max_name_bytes': 32,
                    'unregistered_setting': '17_zero_words', 'unused_and_last_two_bytes': 'NULL'},
                'constraints.logging': common, 'execution.documented': {'command': 'instruction_execution'}}
        else:
            dimensions = {'parameters.typed_operands': {'operands': [operand('n', 'setting_number', type='BIN16', min=1, max=10),
                operand('S', 'filename_prefix', type='STRING_OR_DEVICE')]},
                'effects.documented_operation': {'operation': 'prepend_logging_filename', 'object': 'S_plus_underscore',
                    'result': 'subsequently_created_file_name'},
                'constraints.logging': {**common, 'prefix_ASCII_bytes': [1, 8], 'max_final_name_bytes': 48},
                'execution.documented': {'setting': 'registered', 'execute_before': {'continuous': 'sampling_period',
                    'trigger': 'trigger_condition', 'specification_period': 'period_condition'}},
                'effects.state_filename_prefix': {'SD710': 'prefix_enabled', 'SD711': 'execution_error_cleared_then_set_on_error',
                    'SD712': 'set_when_accepted_clear_when_first_stack_file_created', 'NULL_prefix': 'disable_and_clear_SD710_SD712',
                    'change_prefix': 'execute_NULL_then_new_prefix', 'rewrite_logging_setting': 'clear_SD710_to_SD712'}}
        emit(mid, [symbol], pages, dimensions, conditions,
             {'serial_at_least': 20073, 'configuration_tool_at_least': '1.84N'}, (192, 192))
        rows, _ = _pages(source, mid, 192, 192)
        for model in (m for m in models if m.startswith('L')):
            facts[(model, symbol)] = [_fact(mid, symbol, 'constraints.cpu_availability',
                {'available': False, 'basis': 'added_function_table_LCPU_not_supported'}, rows, (192, 192), model)]
        manuals[mid] = models
    inventory.append(dict(manual='sh080892engj', pages=[92], declared_symbols=['SM1842'],
                          disposition='not_an_instruction', reason='special_relay_precaution'))
    return facts, {'method': METHOD, 'inventory': inventory,
        'sections': dict(Counter(item['disposition'] for item in inventory)),
        'source_present_not_extracted': gaps,
        'facts': {'owners': len(facts), 'total': sum(map(len, facts.values()))},
        'executable_behavior_dsl': False, 'database_mutated': False}

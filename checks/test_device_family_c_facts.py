import json

from knowledge.fact_corpus import resolve

C_CHUNKS = {str(value) for value in range(12247, 12273)}


def payload(model):
    rows = resolve('device_family', ['C'], plc_model=model, task_type='analysis', domain_results=True)
    return [json.loads(row['text'].split('\n', 1)[1]) for row in rows
            if str(row.get('source_chunk_id')) in C_CHUNKS]


def test_fx_counter_ranges_keep_their_cpu_table():
    values = payload('FX3U')
    row = next(value for value in values if 'C0 to C199' in value['value']['address_support'])
    assert row['value']['access'] == 'read/write'
    assert 'C200-C219' in row['value']['address_support']
    assert row['value']['source_constraints']['reset'] == 'RST resets current value to 0 and turns the output contact OFF'


def test_q_counter_has_increment_and_reset_without_inventing_points():
    values = payload('Q03UDV')
    row = next(value for value in values if value['value']['address_support'].startswith('decimal C device numbers'))
    assert row['value']['access'] == 'read/write'
    assert row['value']['source_constraints']['increment'].startswith('OUT C increments')
    assert 'point range' in row['value']['address_support']


def counter_part(model, target, chunk):
    rows = resolve('device_family', [target], plc_model=model, task_type='analysis', domain_results=True)
    return next(json.loads(row['text'].split('\n', 1)[1]) for row in rows
                if str(row.get('source_chunk_id')) == chunk)


def test_counter_coil_and_contact_share_the_c_number():
    coil = counter_part('FX3U', 'CC', '12273')
    contact = counter_part('FX3U', 'CS', '12274')
    assert coil['value']['access'] == 'controlled_by_OUT_C'
    assert coil['value']['source_constraints']['set_by'] == 'OUT C'
    assert contact['value']['access'] == 'read_only_contact'
    assert contact['value']['source_constraints']['reset_by'] == 'RST C'
    assert 'same decimal C number' in coil['value']['address_support']
    assert 'same decimal C number' in contact['value']['address_support']


def test_direct_io_keeps_the_x_y_number_and_access_direction():
    dx = counter_part('Q03UDV', 'DX', '12302')
    dy = counter_part('L02', 'DY', '12307')
    assert dx['value']['access'] == 'direct_input_read'
    assert dx['value']['source_constraints']['mode'] == 'direct_io'
    assert 'same numeric address as the corresponding X device' in dx['value']['address_support']
    assert dy['value']['access'] == 'direct_output_write'
    assert 'same numeric address as the corresponding Y device' in dy['value']['address_support']


def test_timer_coil_and_contact_share_the_t_number():
    coil = counter_part('FX3U', 'TC', '12454')
    contact = counter_part('FX3U', 'TS', '12455')
    assert coil['value']['access'] == 'controlled_by_OUT_T'
    assert coil['value']['source_constraints']['set_by'] == 'OUT T'
    assert contact['value']['access'] == 'read_only_contact'
    assert contact['value']['source_constraints']['on_when'] == 'current value reaches the set value'


def test_zr_is_a_16_bit_file_register_with_shared_capacity():
    zr = counter_part('L02', 'ZR', '12488')
    assert zr['value']['access'] == 'read/write'
    assert zr['value']['source_constraints']['width'] == '16 bits'
    assert zr['value']['source_constraints']['access_unit'] == '16 bits'
    assert 'share' in zr['value']['source_constraints']['shared_capacity']


def test_er_ranges_differ_by_fx3_cpu_and_fx3s_is_unsupported():
    fx3u = counter_part('FX3U', 'ER', '12308')
    fx3s = counter_part('FX3S', 'ER', '12310')
    assert fx3u['value']['access'] == 'read/write'
    assert fx3u['value']['source_constraints']['fx3u_fx3uc']['points'] == 32768
    assert fx3s['value']['access'] == 'not_supported'
    assert fx3s['value']['source_constraints']['supported'] is False


def test_latch_relay_is_internal_and_retained():
    latch = counter_part('Q03UDV', 'L', '12324')
    assert latch['value']['access'] == 'read/write'
    assert latch['value']['source_constraints']['width'] == '1 bit'
    assert latch['value']['source_constraints']['cleared_by'].startswith('latch clear')


def test_link_register_is_sixteen_bit_and_defaults_to_8192_points():
    link = counter_part('Q03UDV', 'W', '12462')
    assert link['value']['access'] == 'read/write'
    assert link['value']['source_constraints']['width'] == '16 bits'
    assert link['value']['source_constraints']['32bit_access'] == 'Wn and Wn+1'
    assert 'default 8192 points' in link['value']['source_constraints']['points']


def test_link_special_devices_are_status_not_general_outputs():
    sb = counter_part('Q03UDV', 'SB', '12418')
    sw = counter_part('L02', 'SW', '12436')
    assert sb['value']['access'] == 'read_only_status'
    assert sw['value']['access'] == 'read_only_status'
    assert sb['value']['source_constraints']['width'] == '1 bit'
    assert sw['value']['source_constraints']['width'] == '16 bits'
    assert 'SB0 to SB7FF' in sb['value']['address_support']
    assert 'SW0 to SW7FF' in sw['value']['address_support']


def test_x_y_are_input_and_output_relays_not_internal_ranges():
    x = counter_part('FX3U', 'X', '12465')
    y = counter_part('Q03UDV', 'Y', '12474')
    assert x['value']['access'] == 'read_only_input'
    assert y['value']['access'] == 'read_write_output'
    assert 'CPU I/O allocation' in x['value']['address_support']
    assert 'external output module' in y['value']['source_constraints']['destination']


def test_st_and_v_keep_their_cpu_specific_roles():
    st = counter_part('Q03UDV', 'ST', '12430')
    fx_v = counter_part('FX3U', 'V', '12456')
    q_v = counter_part('Q03UDV', 'V', '12458')
    assert st['value']['source_constraints']['reset_by'] == 'RST ST clears the current value and turns the contact OFF'
    assert fx_v['value']['source_constraints']['width'] == '16 bits'
    assert fx_v['value']['source_constraints']['32bit_pair'] == 'Vn (high word) with Zn (low word)'
    assert q_v['value']['access'] == 'read_only_contact'
    assert q_v['value']['source_constraints']['use'].startswith('contact only')


def test_sm_and_sd_are_system_defined_with_mixed_access():
    sm = counter_part('Q03UDV', 'SM', '12426')
    sd = counter_part('L02', 'SD', '12424')
    assert sm['value']['access'] == 'system_defined_mixed'
    assert sd['value']['access'] == 'system_defined_mixed'
    assert sm['value']['source_constraints']['width'] == '1 bit'
    assert sd['value']['source_constraints']['width'] == '16 bits'
    assert sm['value']['source_constraints']['user_access'].startswith('mixed')
    assert 'not a free data-register range' in sd['value']['source_constraints']['not_general']


def test_timer_and_data_register_are_sixteen_bit_words():
    timer = counter_part('FX3U', 'T', '12438')
    data = counter_part('FX3U', 'D', '12277')
    assert timer['value']['source_constraints']['coil'].startswith('OUT T')
    assert data['value']['source_constraints']['32bit_access'] == 'Dn and Dn+1'
    assert data['value']['source_constraints']['width'] == '16 bits'


def test_internal_relay_step_relay_and_pointer_are_not_data_registers():
    m = counter_part('FX3U', 'M', '12334')
    s = counter_part('FX3U', 'S', '12401')
    p = counter_part('FX3S', 'P', '12363')
    assert m['value']['access'] == 'read/write'
    assert 'use Y or a link device' in m['value']['source_constraints']['external_output']
    assert s['value']['access'] == 'sfc_step_only'
    assert p['value']['access'] == 'control_pointer_only'
    assert p['value']['source_constraints']['points'] == 255


def test_z_annunciator_interrupt_and_file_register_are_cpu_scoped():
    fx_z = counter_part('FX3U', 'Z', '12477')
    q_z = counter_part('Q03UDV', 'Z', '12485')
    f = counter_part('Q03UDV', 'F', '12313')
    i = counter_part('FX3U', 'I', '12316')
    fx_r = counter_part('FX3G', 'R', '12378')
    q_r = counter_part('Q03UDV', 'R', '12395')
    assert '8 points' in fx_z['value']['source_constraints']['count']
    assert '20 points' in q_z['value']['source_constraints']['count']
    assert f['value']['source_constraints']['system_effect'].startswith('when any F is ON')
    assert i['value']['access'] == 'control_pointer_only'
    assert '24000 points' in fx_r['value']['address_support']
    assert 'block file register' in q_r['value']['address_support']

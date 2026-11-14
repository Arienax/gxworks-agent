import json

from knowledge.fact_corpus import resolve


def fact(target, model):
    rows = resolve('device', [target], plc_model=model, task_type='analysis', domain_results=True)
    return next(json.loads(row['text'].split('\n', 1)[1]) for row in rows
                if row.get('fact_target') == target or json.loads(row['text'].split('\n', 1)[1]).get('target') == target)


def test_error_flag_keeps_each_cpu_error_relay_set():
    fx3 = fact('M8004', 'FX3U')
    fx1s = fact('M8004', 'FX1S')
    assert fx3['value']['semantic_facts']['monitored_relays'] == [
        'M8060', 'M8061', 'M8064', 'M8065', 'M8066', 'M8067']
    assert 'M8063' in fx1s['value']['semantic_facts']['monitored_relays']


def test_clock_rtc_and_power_failure_flags_stay_source_scoped():
    clock = fact('M8011', 'FX3U')
    correction = fact('M8017', 'FX3U')
    power = fact('M8008', 'FX3U')
    assert clock['value']['semantic_facts']['period_ms'] == 10
    assert 'nearest minute' in correction['value']['semantic_facts']['off_to_on']
    assert 'STOP' in power['value']['semantic_facts']['timeout']


def test_math_and_parallel_link_flags_are_not_interchanged():
    zero = fact('M8020', 'FX3U')
    parallel = fact('M8072', 'FX3U')
    assert zero['value']['semantic_facts']['function'] == 'zero_flag'
    assert parallel['value']['semantic_facts']['function'] == 'parallel_link_operating'


def test_measurement_and_block_comparison_flags_keep_their_own_registers():
    mode = fact('M8080', 'FX3G')
    compare = fact('M8090', 'FX3U')
    assert mode['value']['semantic_facts']['related_devices'][0] == 'D8074'
    assert 'BKCMP' in compare['value']['semantic_facts']['on']


def test_high_speed_and_communication_flags_keep_dedicated_registers():
    table_done = fact('M8131', 'FX3U')
    receive = fact('M8123', 'FX2C')
    monitor = fact('M8147', 'FX3G')
    assert table_done['value']['semantic_facts']['related_devices'] == ['D8130']
    assert receive['value']['semantic_facts']['related_devices'] == ['D8123']
    assert 'Y000' in monitor['value']['semantic_facts']['on']


def test_pulse_catch_and_eight_bit_mode_keep_model_conditions():
    pulse = fact('M8170', 'FX3U')
    legacy_pulse = fact('M8173', 'FX2N')
    eight_bit = fact('M8161', 'FX3U')
    assert pulse['value']['semantic_facts']['ei_required'] == 'FX3U/FX3UC only'
    assert 'FX2N' in legacy_pulse['value']['semantic_facts']['ei_required']
    assert eight_bit['value']['semantic_facts']['function'] == 'eight_bit_process_mode'


def test_network_aliases_and_edge_count_selection_stay_explicit():
    master = fact('M8183', 'FX1S')
    counter = fact('M8198', 'FX3U')
    assert master['value']['semantic_facts']['alias_on_fx1s'] == 'M504'
    assert counter['value']['semantic_facts']['on'] == '4 edge count'
    assert 'C251' in counter['value']['semantic_facts']['counters']


def test_dual_use_relays_split_by_cpu_and_keep_failure_registers():
    pulse = fact('M8077', 'FX3G')
    trace = fact('M8077', 'FX2N')
    bfm = fact('M8318', 'FX3U')
    assert pulse['value']['semantic_facts']['function'] == 'pulse_width_period_measurement_x001'
    assert trace['value']['semantic_facts']['function'] == 'sampling_trace_in_progress'
    assert bfm['value']['semantic_facts']['related_devices'] == ['D8318', 'D8319']


def test_duty_and_positioning_flags_keep_output_axis_bindings():
    duty = fact('M8330', 'FX3U')
    forward = fact('M8343', 'FX3G')
    dog = fact('M8365', 'FX3G')
    assert duty['value']['semantic_facts']['related_devices'] == ['D8330']
    assert forward['value']['semantic_facts']['function'] == 'y000_forward_limit'
    assert dog['value']['semantic_facts']['function'] == 'y002_dog_signal_logic_reverse'


def test_counter_status_and_y003_axis_keep_their_own_bindings():
    status = fact('M8382', 'FX3U')
    limit = fact('M8373', 'FX3U')
    ring = fact('M8398', 'FX3G')
    assert status['value']['semantic_facts']['counters'] == ['C237', 'C242', 'C245']
    assert limit['value']['semantic_facts']['function'] == 'y003_forward_limit'
    assert ring['value']['semantic_facts']['related_devices'] == ['D8398', 'D8399']


def test_communication_mode_dependent_relays_keep_both_functions():
    dual = fact('M8402', 'FX3U')
    unused = fact('M8406', 'FX3G')
    functions = dual['value']['semantic_facts']['functions']
    assert functions['rs2'] == 'send request'
    assert functions['modbus'] == 'communication error'
    assert unused['value']['semantic_facts']['function'] == 'not_used'


def test_dvit_and_ip_storage_relays_keep_instruction_and_axis_bindings():
    dvit = fact('M8460', 'FX3U')
    write = fact('M8492', 'FX3G')
    assert dvit['value']['semantic_facts']['axis'] == 'Y000'
    assert dvit['value']['semantic_facts']['related_devices'] == ['D8336']
    assert write['value']['semantic_facts']['function'] == 'ip_address_storage_area_write_request'


def test_potentiometer_registers_are_system_written_and_inverter_channel_is_explicit():
    vr1 = fact('D8030', 'FX1S')
    inverter = fact('M8151', 'FX3G')
    assert vr1['value']['access'] == 'system'
    assert vr1['value']['semantic_facts']['registers'] == {'VR1': 'D8030'}
    assert inverter['value']['semantic_facts']['channel'] == 1











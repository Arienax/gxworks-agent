"""Project-authored examples expressed with the existing PLC IR, not manual code."""
from __future__ import annotations


def build_cases():
    from plc.ir import build_plc_ir

    def bit(address, negate=False):
        return {"type": "NC" if negate else "NO", "address": address}

    def output(address):
        return {"type": "COIL", "address": address}

    def app(opcode, *operands):
        return {"type": "APP_INSTR", "opcode": opcode, "operands": list(operands)}

    def ladder(rows):
        return {"device_comments": {}, "rungs": [{"rung_id": i, "header_element": None, "shared_inputs": [],
            "branches": [{"branch_id": 1, "y_offset_level": 0, "inputs": ins, "outputs": outs}]}
            for i, (ins, outs) in enumerate(rows, 1)]}

    cases = []

    def add(identity, family, requirement, rows, initial, frames, expected, *, boundary=False, limitation=""):
        program = build_plc_ir(ladder(rows), plc_model="FX3U")
        cases.append({"id": identity, "family": family, "variant": "boundary" if boundary else "normal",
            "models": ["FX3U"], "requirement": requirement, "program": program, "initial": initial,
            "frames": [{"inputs": frame} for frame in frames], "expected": expected, "origin": "project_authored",
            "model_conditions": {"cpu": "FX3U", "other_models": "unverified"},
            "verification": {"static_ir": "pending", "bounded_scan": "pending", "native_compile": "not_performed",
                "native_simulation": "not_performed", "hardware": "not_performed"}, "limitation": limitation})

    add("permission_gate", "logic_latch", "加工许可与防护门信号均有效时，才点亮待加工指示。",
        [([bit("X4"), bit("X5")], [output("Y2")])], {}, [{"X4": True, "X5": True}], {"Y2": True})
    add("permission_gate_open", "logic_latch", "防护门信号失效时，即使加工许可保持也应撤销指示。",
        [([bit("X4"), bit("X5")], [output("Y2")])], {}, [{"X4": True, "X5": False}], {"Y2": False}, boundary=True)
    add("batch_sum", "arithmetic_compare", "把当前批次的 17 件与补料的 6 件相加，写入批次总数。",
        [([bit("X4")], [app("ADD", "D20", "D21", "D22")])], {"D20": 17, "D21": 6, "D22": 0}, [{"X4": True}], {"D22": 23})
    add("disabled_sum_retains", "arithmetic_compare", "补料计算未获许可时，不覆盖已有批次数。",
        [([bit("X4")], [app("ADD", "D20", "D21", "D22")])], {"D20": 17, "D21": 6, "D22": 41}, [{"X4": False}], {"D22": 41}, boundary=True)
    add("cycle_count", "timer_counter", "每个采样周期有计件许可时，将软件批次计数加一。",
        [([bit("X4")], [app("INC", "D24")])], {"D24": 8}, [{"X4": True}], {"D24": 9},
        limitation="这是按扫描许可计数，不宣称为物理输入边沿计数或硬件高速计数。")
    add("cycle_count_disabled", "timer_counter", "计件许可关闭后，软件批次计数保持原值。",
        [([bit("X4")], [app("INC", "D24")])], {"D24": 8}, [{"X4": False}], {"D24": 8}, boundary=True)
    add("recipe_snapshot", "data_move_indexing", "配方确认时，把设定值 73 保存为本批次快照。",
        [([bit("X4")], [app("MOV", "D30", "D31")])], {"D30": 73, "D31": 12}, [{"X4": True}], {"D31": 73},
        limitation="只覆盖单字快照。间接寻址和跨字边界需要各自的已核验规则。")
    add("recipe_snapshot_disabled", "data_move_indexing", "配方未确认时，即使设定值变化也保留本批次快照。",
        [([bit("X4")], [app("MOV", "D30", "D31")])], {"D30": 73, "D31": 12}, [{"X4": False}], {"D31": 12}, boundary=True)
    add("phase_advance", "sequence_loop", "工序完成信号有效时，把阶段号切换到 30。",
        [([bit("X4")], [app("MOV", "K30", "D40")])], {"D40": 20}, [{"X4": True}], {"D40": 30},
        limitation="只说明一次有条件的状态写入，不构成 FOR/NEXT 循环或完整状态机的验证。")
    add("phase_hold", "sequence_loop", "工序完成信号尚未到达时，阶段号停留在 20。",
        [([bit("X4")], [app("MOV", "K30", "D40")])], {"D40": 20}, [{"X4": False}], {"D40": 20}, boundary=True)
    add("axis_request_gate", "high_speed_positioning", "轴已就绪且动作许可有效时，形成内部定位请求。",
        [([bit("X4"), bit("X5")], [output("M240")])], {}, [{"X4": True, "X5": True}], {"M240": True},
        limitation="仅为定位调用前的请求门控。未调用脉冲指令，不证明轴运动、端子或完成标志行为。")
    add("axis_not_ready", "high_speed_positioning", "轴就绪信号缺失时，不形成定位请求。",
        [([bit("X4"), bit("X5")], [output("M240")])], {}, [{"X4": True, "X5": False}], {"M240": False}, boundary=True,
        limitation="仅验证请求门控；脉冲指令及硬件行为另行验证。")
    add("receive_ack", "communication", "接收缓冲已提交且校验有效时，置有效数据指示。",
        [([bit("X4"), bit("X5")], [output("M250")])], {}, [{"X4": True, "X5": True}], {"M250": True},
        limitation="输入代表调用方提供的缓冲和校验状态；没有执行串口或网络协议。")
    add("receive_invalid", "communication", "收到缓冲但校验失败时，不允许消费该批数据。",
        [([bit("X4"), bit("X5")], [output("M250")])], {}, [{"X4": True, "X5": False}], {"M250": False}, boundary=True,
        limitation="仅验证数据消费许可；实际通信指令、超时及缓冲布局仍未验证。")
    add("fault_blocks_request", "error_handling", "故障状态有效时，即使有启动请求也撤销运行许可。",
        [([bit("X4"), bit("M260", True)], [output("M261")])], {"M260": True}, [{"X4": True}], {"M261": False})
    add("fault_clear_is_not_restart", "error_handling", "故障清除但没有新的启动请求时，运行许可仍关闭。",
        [([bit("X4"), bit("M260", True)], [output("M261")])], {"M260": False}, [{"X4": False}], {"M261": False}, boundary=True)
    # Rebuild each cross-model IR from the same authored requirement, admitting
    # only the exact control/transfer subset already reviewed by Core.
    from plc.runtime_semantics import control_runtime_facts
    from plc.ir import ir_to_ladder
    from plc.validation import SUPPORTED_PLC_MODELS
    import copy
    base = [c for c in cases if c['id'] in {'permission_gate', 'permission_gate_open',
        'recipe_snapshot', 'recipe_snapshot_disabled'}]
    for model in ('FX1S', 'FX1N', 'FX2N', 'FX3S', 'FX2C', 'Q03UDE', 'Q03UDV',
                  'Q12PH', 'Q12PRH', 'L02', 'L26-BT', 'FX5U'):
        if model not in SUPPORTED_PLC_MODELS:
            continue
        forms = set(control_runtime_facts(model).get('control', {}).get('forms', []))
        for original in base:
            if not {'LD', 'AND', 'OUT', 'MOV'}.issubset(forms):
                continue
            case = copy.deepcopy(original)
            case['id'] += '_' + model.lower()
            case['models'] = [model]
            case['program'] = build_plc_ir(ir_to_ladder(original['program']), plc_model=model)
            case['model_conditions'] = {'cpu': model, 'other_models': 'unverified',
                'basis': 'existing_reviewed_control_and_direct_word_transfer_subset'}
            cases.append(case)
    for boundary, elapsed in ((False, 300), (True, 299)):
        identity = 'timer_at_threshold' if not boundary else 'timer_before_threshold'
        rows = [([bit('X4')], [{'type': 'TIMER', 'address': 'T0', 'value': 'K3'}]),
                ([bit('T0')], [output('Y3')])]
        add(identity, 'timer_counter', '许可持续有效时，T0 在显式经过 300 ms 后接通；此前保持断开。',
            rows, {'T0': 0, 'T0.contact': False}, [{'X4': True}], {'Y3': not boundary}, boundary=boundary,
            limitation='显式提供经过时间，不把扫描次数换算成时间；未验证实机定时精度。')
        cases[-1]['frames'][0]['timer_elapsed_ms'] = {'T0': elapsed}
    return {"schema_version": 1, "origin": "project_authored", "cases": cases,
        "coverage_gaps": ["other_cpu_models", "hardware_counters", "indirect_addressing",
            "FOR_NEXT_execution", "pulse_instruction_execution", "communication_protocol_execution"]}

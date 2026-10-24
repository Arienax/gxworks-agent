"""Authored FBD facts from the existing private source index.

The compiler checks local source identities and passages; runtime consumers
receive typed values and conditions, never copied manual chapters.
"""
from copy import deepcopy
from decimal import Decimal, localcontext


def reviewed_fbd_features(source):
    from tools.fact_condition_review import _source, header_availability

    def ref(manual, revision, page, section, *witnesses):
        return _source(source, manual, revision, page, section, list(witnesses))

    fundamentals = 'structured_fundamentals_o'
    models = source.execute('SELECT plc_models FROM manuals WHERE manual_id=?', (fundamentals,)).fetchone()[0].split(',')
    constants = ref(fundamentals, 'O', 36, 'Expressing methods of constants', 'K123', 'T#1h')
    types = ref(fundamentals, 'O', 34, 'Data types', '-32768 to 32767', 'T#-24d20h31m23s648ms')
    generic = ref(fundamentals, 'O', 35, 'Data types', 'ANY_NUM', 'ANY16', 'ANY32')
    block_labels = ref(fundamentals, 'O', 24, 'Ladder blocks', 'jump target')
    graphical = ref(fundamentals, 'O', 89, 'Ladder symbols in structured ladder/FBD language', 'Return', 'Use Macrocode')
    time = ref(fundamentals, 'O', 49, 'Time data', 'T#10d20h30m40s567ms')
    en = ref(fundamentals, 'O', 29, 'EN and ENO', 'EN', 'ENO')
    order = ref(fundamentals, 'O', 91, 'Executing order', 'MOV')
    instance = ref(fundamentals, 'O', 26, 'Functions, function blocks, and operators', 'instance')
    labels = ref(fundamentals, 'O', 32, 'Label classes', 'VAR_INPUT', 'VAR_IN_OUT')
    inout = ref('gxworks2_structured_ag', 'AG', 196, '10.5.3 Considerations of Compilation',
                'constants can be used for the input side', 'VAR_IN_OUT')
    options = ref('gxworks2_structured_ag', 'AG', 57, '4.3.2 Setting properties of functions and function blocks',
                  'Use Macrocode', 'Use MC/MCR', 'Use EN/ENO')
    pins = ref('gxworks2_structured_ag', 'AG', 146,
               '8.6.3 Changing the number of arguments for functions and function blocks', 'Number of Pins')
    timer = ref('fxcpu_application_k', 'K', 254, '14.7 TON(_E), TON_10(_E) / On delay timer', '100 ms', '10 ms')

    rows = []
    def add(target, aliases, value, references, owners=None):
        owners = owners or models
        rows.append(dict(target=target, language='fbd', models=owners, aliases=aliases, facts=[dict(
            id=target.lower(), dimension='parameters.' + target.lower().replace('.', '_'),
            value=deepcopy(value), status='source_verified', verification='source_checked',
            sources=references, scope=dict(models=owners, forms=[target], languages=['fbd']), depends_on=[])]))

    scalar = ['BOOL', 'INT', 'DINT', 'WORD', 'DWORD', 'REAL', 'LREAL', 'STRING', 'TIME']
    generics = {'ANY_INT': ['INT', 'DINT'], 'ANY_REAL': ['REAL', 'LREAL'],
                'ANY_NUM': ['INT', 'DINT', 'REAL', 'LREAL'], 'ANY_BIT': ['BOOL', 'WORD', 'DWORD'],
                'ANY16': ['INT', 'WORD'], 'ANY32': ['DINT', 'DWORD', 'REAL'],
                'ANY_SIMPLE': scalar, 'ANY': scalar}
    with localcontext() as decimal_context:
        decimal_context.prec = 1100
        real_ranges = {name: [str(Decimal(2) ** low),
                              str((2 - Decimal(2) ** -fraction) * Decimal(2) ** high)]
                       for name, low, high, fraction in [('REAL', -126, 127, 23), ('LREAL', -1022, 1023, 52)]}
    add('FBD.LITERALS', ['FBD 常量', 'FBD literal', '常量写法', 'INT#', 'T#', '时间常数'], {
        'forms': ['boolean', 'binary', 'octal', 'decimal', 'hexadecimal', 'real', 'string', 'time'],
        'integer_types': ['INT', 'DINT', 'WORD', 'DWORD'], 'real_ranges': real_ranges,
        'time_units_ms': {'D': 86400000, 'H': 3600000, 'M': 60000, 'S': 1000, 'MS': 1},
        'time_range_ms': [-2147483648, 2147483647], 'string_max_characters': 255,
        'examples': {'boolean': ['TRUE', 'FALSE', '0', '1'], 'decimal': ['0', 'K0', 'K-1'],
                     'binary': ['2#0010'], 'octal': ['8#337'], 'hexadecimal': ['16#FF', 'HFF'],
                     'real': ['2.34', 'E2.34'], 'string': ["'ABC'", '"ABC"'], 'time': ['T#1s', 'T#100ms']},
        'target_product': 'GX Works2', 'typed_iec_prefixes_admitted': False,
    }, [constants, types, time])
    add('FBD.TYPES', ['FBD 类型', 'FBD types', 'ANY16', 'ANY32', '泛型端口'], {
        'scalar_types': scalar, 'generic_types': generics, 'any_includes_composites': True,
        'generic_storage_bits': {'ANY16': 16, 'ANY32': 32},
        'implicit_casts': [], 'time_is_integer_cast': False,
        'fx_type_conditions': {'REAL': ['FX3S', 'FX3G', 'FX3GC', 'FX3U', 'FX3UC'],
                               'STRING': ['FX3U', 'FX3UC'], 'LREAL': []},
        'lreal_cpu_condition': 'universal_q_or_l',
        'supported_types_by_model': {model: [kind for kind in scalar if
            (kind != 'LREAL' or model.startswith('L') or model.startswith('Q') and 'U' in model)
            and (not model.startswith('FX') or kind != 'REAL' or model in ('FX3S', 'FX3G', 'FX3GC', 'FX3U', 'FX3UC'))
            and (not model.startswith('FX') or kind != 'STRING' or model in ('FX3U', 'FX3UC'))]
            for model in models},
    }, [types, generic])
    numeric = generics['ANY_NUM']
    operators = {name: dict(inputs=numeric, output=numeric, same_input_type=True, same_result_type=True)
                 for name in ('ADD', 'SUB', 'MUL', 'DIV')}
    # FBD symbol contracts are owned by the FBD function/operator chapters,
    # not borrowed from the ST precedence table.
    for name in ('EQ', 'NE', 'LT', 'LE', 'GT', 'GE'):
        operators[name] = dict(inputs=scalar, output=['BOOL'], same_input_type=True, same_result_type=False)
    for name in ('AND', 'OR', 'XOR', 'NOT'):
        operators[name] = dict(inputs=generics['ANY_BIT'], output=generics['ANY_BIT'],
                               same_input_type=True, same_result_type=True)
    operator_refs = [
        ref('fxcpu_application_k', 'K', 265, '15.1 ADD / Addition', 'ANY_NUM'),
        ref('fxcpu_application_k', 'K', 271, '15.7 AND / Logical product', 'ANY_BIT'),
        ref('fxcpu_application_k', 'K', 277, '15.12 GE / Comparison', 'ANY_SIMPLE'),
        ref('sh080784engn', 'N', 207, 'Addition', 'ANY_NUM'),
        ref('sh080784engn', 'N', 215, 'Boolean AND, boolean OR, boolean exclusive OR, and boolean NOT', 'ANY_BIT'),
        ref('sh080784engn', 'N', 217, 'Comparison', 'ANY_SIMPLE'),
    ]
    for page, section, kind in [
        (266, '15.2 SUB / Subtraction', 'ANY_NUM'), (267, '15.3 MUL / Multiplication', 'ANY_NUM'),
        (268, '15.4 DIV / Division', 'ANY_NUM'), (272, '15.8 OR / Logical sum', 'ANY_BIT'),
        (273, '15.9 XOR / Exclusive logical sum', 'ANY_BIT'), (275, '15.10 NOT / Logical negation', 'ANY_BIT'),
        (276, '15.11 GT / Comparison', 'ANY_BIT'), (278, '15.13 EQ / Comparison', 'ANY_SIMPLE'),
        (279, '15.14 LE / Comparison', 'ANY_SIMPLE'), (280, '15.15 LT / Comparison', 'ANY_SIMPLE'),
        (281, '15.16 NE / Comparison', 'ANY_SIMPLE')]:
        operator_refs.append(ref('fxcpu_application_k', 'K', page, section, kind))
    add('FBD.OPERATORS', ['FBD 运算', 'FBD 比较', 'FBD operators'], {
        'operators': operators, 'unresolved_overload': 'retain_type_domain',
        'standard_function_aliases': {name + '_E': name for name in operators},
        'source_conflicts': {'GT': {'models': [model for model in models if model.startswith('FX')],
            'reason': 'FX GT table says ANY_BIT while its operation text and Q/L comparison contract cover numeric data'}},
        'ordinary_data_wire_is_execution_enable': False,
    }, operator_refs + [generic] + [ref('fxcpu_application_k', 'K', page, section, kind) for page, section, kind in [
        (163,'7.1 ADD_E / Addition','ANY_NUM'), (165,'7.2 SUB_E / Subtraction','ANY_NUM'),
        (167,'7.3 MUL_E / Multiplication','ANY_NUM'), (169,'7.4 DIV_E / Division','ANY_NUM'),
        (183,'9.1 AND_E / Logical product','ANY_BIT'), (185,'9.2 OR_E / Logical sum','ANY_BIT'),
        (187,'9.3 XOR_E / exclusive logical sum','ANY_BIT'), (189,'9.4 NOT(_E) / logical negation','ANY_BIT'),
        (203,'11.1 GT_E / Comparison','ANY_SIMPLE'), (205,'11.2 GE_E / Comparison','ANY_SIMPLE'),
        (207,'11.3 EQ_E / Comparison','ANY_SIMPLE'), (209,'11.4 LE_E / Comparison','ANY_SIMPLE'),
        (211,'11.5 LT_E / Comparison','ANY_SIMPLE'), (213,'11.6 NE_E / Comparison','ANY_SIMPLE')]])
    add('FBD.CALL.BINDING', ['FBD 参数', 'FBD IN_OUT', 'FBD 调用', 'FBD VAR_INPUT'], {
        'var_input_writable': False, 'output_requires_writable_target': True,
        'constant_input_formal_classes': ['VAR_INPUT CONSTANT'],
        'extendable_arguments': 'current_source_VAR_IN_EXT_arity',
        'omitted_arguments': 'current_source_optional_contract_only',
        'inout_input_constant': {
            'fbd': {'BOOL': {'unused': True, 'written': True}, 'other': {'unused': True, 'written': False}},
            'st': {'BOOL': {'unused': True, 'written': True}, 'other': {'unused': True, 'written': False}},
            'ladder': {'BOOL': {'unused': True, 'written': True}, 'other': {'unused': False, 'written': False}},
        }, 'inout_constant_conditions': ['caller_language', 'callee_language', 'formal_type', 'callee_output_usage'],
        'inout_input_and_output_are_distinct_bindings': True,
        'same_generic_value_type': ['MOVE','MOVE_E','SEL','SEL_E','MUX','MUX_E'],
    }, [inout, labels, pins,
        ref('fxcpu_application_k','K',175,'7.7 MOVE(_E) / Move operation','ANY'),
        ref('fxcpu_application_k','K',192,'10.1 SEL(_E) / Selection','ANY'),
        ref('fxcpu_application_k','K',200,'10.5 MUX(_E) / Multiplexer','ANY')])
    add('FBD.EXECUTION', ['FBD 顺序', 'FBD EN/ENO', 'FBD execution order'], {
        'series_order': 'left_to_right', 'parallel_order': 'top_to_bottom', 'network_order': 'stored_order',
        'dependency_kinds': ['data', 'enable', 'shared_storage', 'instance_state'],
        'en_eno': {'enabled_success': 'valid_result', 'enabled_error': 'undefined_result',
                   'disabled': 'no_execution', 'disabled_eno': False},
        'result_use_requires_enable_contract': True, 'feedback_rule': 'requires_state_and_order_contract',
    }, [order, en])
    add('FBD.INSTANCE.STATE', ['FBD 实例', 'FB 共享状态', 'Use Macrocode', 'Use MC/MCR'], {
        'same_instance_shares_state': True, 'repeated_instance_calls_require_order': True,
        'distinct_instances_have_distinct_state': True,
        'disabled_state_depends_on': ['callee_language', 'use_macrocode', 'use_mc_mcr', 'use_en_eno'],
        'macrocode_without_mc_mcr': 'output_variables_retained_when_EN_off',
        'use_mc_mcr_requires': ['use_macrocode', 'use_en_eno'],
        'en_disabled_is_in_false': False, 'en_disabled_is_reset': False,
    }, [instance, options])
    add('FBD.SYMBOLS', ['FBD 标签', 'FBD 数组', 'FBD 结构成员'], {
        'label_binding': 'current_local_global_scope_and_hiding_option',
        'device_storage_and_declared_value_type_are_distinct': True,
        'composite_identity': 'declaration_source_and_shape',
        'array_access_requires_declared_bounds': True, 'arbitrary_st_expression_is_terminal': False,
    }, [labels, types])
    add('FBD.CONTROL_FLOW', ['FBD 跳转', '网络标签', 'FBD Return', '并联触点'], {
        'jump_target': 'ladder_block_label_in_same_pou',
        'relay_series_operation': 'AND', 'relay_parallel_operation': 'OR',
        'relay_symbols_mode': 'structured_ladder', 'pure_fbd_relay_symbols': False,
        'return_program': 'end_pou', 'return_function': 'return_to_caller',
        'return_fb_depends_on': ['use_macrocode'],
        'return_macro_fb': 'end_pou', 'return_subroutine_fb': 'return_to_caller',
        'network_header_label_is_control_flow': True,
    }, [block_labels, graphical, order])
    add('FBD.PROJECT', ['FBD 工程条件', 'FBD 编译选项'], {
        'required_context': ['cpu_members', 'project_mode', 'pou_kind', 'source_identity', 'task_scopes',
                             'declarations', 'callable_interfaces', 'compile_options', 'operation_capabilities'],
        'interface_change_checks': 'final_joint_project_and_affected_callers',
        'available_interface_grants_write_capability': False,
        'unknown_fb_options': 'report_missing_conditions_not_default_values',
    }, [options, inout])
    supported = [model for model, state in header_availability(source, timer)['model_states'].items() if state == 'supported']
    add('FBD.TIMERS', ['TON', 'TON_10', 'FBD 定时精度', 'FBD timer resolution'], {
        'resolution_ms': {'TON': 100, 'TON_E': 100, 'TON_10': 10, 'TON_10_E': 10},
        'round_requested_time': False, 'disabled_behavior_requires_block_contract': True,
    }, [timer], supported)
    return rows

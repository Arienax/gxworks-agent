"""Exact AB individual stop-event relations; not a complete PWM timing oracle.

Short-phase and late-stop statements are complete conditional source claims.
Their inclusive guards must not be made disjoint by invented priority. The four early-stop delay statements additionally require the same-edition
Chinese Y diagram and revision-page evidence. The negative-value conjunction is a sufficient-only antecedent; its local
16-bit exception never grants whole-instruction validity. No rule grants a physical timing or composition capability.
"""
from hashlib import sha256
import re
from tools.fact_fx5_pwm_table import parse_pwm_hardware_table

CPU_MODELS = ('FX5S', 'FX5UJ', 'FX5U', 'FX5UC')
FORMS = {'16 bit binary pulse width modulation': ('PWM', 813),
         '32 bit binary pulse width modulation': ('DPWM', 820)}
REVIEWED = {('DPWM', 824, '3c8aed99290aabe4b34ad3a6fd35e640680afd1300eb0eaec814734a73901cb8'): ('ON',
                                                                                     'phase_end',
                                                                                     'condition_precautions_p824_r5_c0'),
 ('DPWM', 824, '7e78b4f4b74105ab60c44d5eb9425e2e1a17e6f3728f5adcddf9593833a4abc0'): ('ON',
                                                                                     'late_stop',
                                                                                     'condition_precautions_p824_r5_c3'),
 ('DPWM', 825, '30aba1edf997b50bae1a27fac8a6286f6250e1c2ac08020a68e1e886fd44956b'): ('OFF',
                                                                                     'late_stop',
                                                                                     'condition_precautions_p825_r10_c3'),
 ('DPWM', 825, '8a8aa25f0b1cbab143d0325f9b82c6949d01726339eae9c8f1b6e432ac255fc7'): ('OFF',
                                                                                     'phase_end',
                                                                                     'condition_precautions_p825_r10_c5'),
 ('PWM', 817, '7e78b4f4b74105ab60c44d5eb9425e2e1a17e6f3728f5adcddf9593833a4abc0'): ('ON',
                                                                                    'late_stop',
                                                                                    'condition_precautions_p817_r4_c3'),
 ('PWM', 817, '8f20c7f54161d590eefcbe4b675ed26885cb33d8455bb28770928c19ee5d77c2'): ('ON',
                                                                                    'phase_end',
                                                                                    'condition_precautions_p817_r4_c0'),
 ('PWM', 818, '30aba1edf997b50bae1a27fac8a6286f6250e1c2ac08020a68e1e886fd44956b'): ('OFF',
                                                                                    'late_stop',
                                                                                    'condition_precautions_p818_r10_c0'),
 ('PWM', 818, '3159cbceb2107c7a808d8aa469ee33746f5d2a31de5dade25a83867eaffc5bc0'): ('OFF',
                                                                                    'phase_end',
                                                                                    'condition_precautions_p818_r5_c0')}


# The Chinese Y figures say 'after 5 microseconds'; AB supplies the explicit
# stop-command anchor. This is a documented nominal delay, not a measured
# physical switching tolerance. All source guards retain their inclusive limits.
EARLY_REVIEWED = {
    ('PWM', 817, 'ebfc0c1613ff2e76ce938990fe154cd078040a673becbd5166cd88200b7fc58a'):
        ('ON', 'early_stop', 'condition_precautions_p817_r4_c1'),
    ('PWM', 818, 'b7525a6aad21a451fa5013f2f5bff8bc0c60281084cbe7d88a42a0e8bd074418'):
        ('OFF', 'early_stop', 'condition_precautions_p818_r7_c0'),
    ('DPWM', 824, 'ebfc0c1613ff2e76ce938990fe154cd078040a673becbd5166cd88200b7fc58a'):
        ('ON', 'early_stop', 'condition_precautions_p824_r5_c1'),
    ('DPWM', 825, '36f5c24b5461f527c04fe8cac8b30d604b5ceada643087e642b28dfaf9354e46'):
        ('OFF', 'early_stop', 'condition_precautions_p825_r10_c4'),
}
NEGATIVE_CLAUSE_SHA256 = '1782d9c86cdfdac7127cd7c56a7a82d5e2cb8aa4a8825cc08460796990c81151'
CHINESE_PAGES = {817: 781, 818: 782, 824: 787, 825: 788}


def _normalized(text):
    return re.sub(r'\s+', '', text.replace('[GLYPH-F06D]', 'μ').replace('µ', 'μ'))


def review_pwm_stop_clause(kind, clause, *, section, block, declared, value=None):
    """Return auxiliary reviews for twelve exact source identities.

    The parent keeps source identity and any separate AB/AG hardware conflicts.
    A source-verified individual rule grants no waveform execution capability.
    """
    if kind != 'precautions' or not isinstance(clause, str):
        return None
    scope = FORMS.get(section.get('section'))
    if scope is None or not section.get('outline', '').startswith('PART 3 CPU MODULE INSTRUCTIONS >'):
        return None
    form, first = scope
    if section.get('first_page') != first or not {'s1', 's2', 'd'} <= set(declared):
        return None
    text = re.sub(r'\s+', ' ', section.get('text', ''))
    if not re.search(r'ENO\s*:=\s*' + form + r'\(EN,\s*s1,\s*s2,\s*d\)', text):
        return None
    if not all(re.search(r'(?<![A-Za-z0-9])' + cpu + r'(?![A-Za-z0-9])', text) for cpu in CPU_MODELS):
        return None
    key = (form, block.get('page'), sha256(_normalized(clause).encode()).hexdigest())
    if key == ('DPWM', 824, NEGATIVE_CLAUSE_SHA256):
        return {'rules': [{
                    'kind': 'PWM_negative_input_qualification',
                    'instruction': 'DPWM',
                    'when': {'all': [{'subject': {'operand': name}, 'operator': '<', 'value': 0}
                                     for name in ('s1', 's2')]},
                    'implication': 'sufficient_only_not_necessary_or_exclusive',
                    'effect': {'operation_error': True},
                    'local_form_exception': {'instruction': 'PWM',
                        'error_from_this_negative_check': False,
                        'other_error_conditions_still_apply': True,
                        'whole_instruction_validity': 'not_established',
                        'input_representation_and_literal_admissibility': 'not_established'},
                    'independent_error_domain': {'logical_connective': 'OR',
                        'subjects': [{'operand': 's1'}, {'operand': 's2'}],
                        'operator': '<', 'value': 1,
                        'binding': 'separate_existing_error_table_rule_not_replaced'}}],
                'forms': ['DPWM'], 'full_clause_review': True,
                'additional_pages': [820, 824, 826],
                'additional_sources': [{'manual_id': 'fx5_programming_zh_y', 'manual_number': 'JY997D58901',
                                        'revision': 'Y', 'pdf_pages': [783, 787, 789, 1402]}],
                'dimension': 'constraints.documented_precautions',
                'value_updates': {'subjects': [{'operand': name} for name in ('s1', 's2')],
                    'operand_binding_required': True, 'context_binding': 'resolved',
                    'numeric_values': [], 'ranges': [], 'comparisons': [], 'source_formulas': [],
                    'unresolved_glyphs': [], 'unbound_condition_markers': [],
                    'unparsed_input_qualification': False, 'device_state_conditions': [],
                    'operand_device_conditions': [], 'cpu_models': list(CPU_MODELS),
                    'knowledge_kind': 'manual_behavior',
                    'semantic_role': 'sufficient_error_condition_with_local_form_exception',
                    'source_relation_identity': 'condition_precautions_p824_r1_c0',
                    'core_execution_capability': 'not_established',
                    'binding_basis': 'AB_conjunction_and_local_exception_with_Chinese_Y_error_domain'}}
    entry = REVIEWED.get(key) or EARLY_REVIEWED.get(key)
    if entry is None:
        return None
    phase, branch, identity = entry
    duration = {'kind': 'configured_time_to_microseconds',
                'value': {'operand': 's1'} if phase == 'ON' else
                         {'operator': '-', 'left': {'operand': 's2'}, 'right': {'operand': 's1'}},
                'unit_from': 'PWM_channel_time_unit_configuration',
                'unknown_configuration': 'unverified',
                'binding_time': 'current_output_phase',
                'runtime_updated_settings': 'requires_current_phase_settings_not_initial_operand_snapshot'}
    guard = {'phase_duration_us': {'operator': '<=' if branch == 'phase_end' else '>=', 'value': 5}}
    if branch in ('late_stop', 'early_stop'):
        guard['elapsed_current_phase_us'] = {'operator': '>=' if branch == 'late_stop' else '<=', 'value': 5}
    rule = {'kind': 'PWM_conditional_stop_event', 'phase': phase,
            'trigger': {'event': 'output_stop_command', 'during': 'current_output_pulse_phase',
                        'driver_equivalences': 'not_established'},
            'phase_duration': duration, 'when': guard,
            'effect': {'stop_event': 'current_phase_end' if branch == 'phase_end' else 'with_output_stop_command'},
            'composition': {'inclusive_guards_preserved': True, 'branch_priority': 'not_established',
                            'multiple_applicable_rules': 'require_consistent_consequence_or_unverified'}}
    result = {'rules': [rule], 'forms': [form], 'full_clause_review': True,
              'additional_pages': [first, block['page']], 'dimension': 'execution.documented_stop_events',
              'value_updates': {'subjects': [{'operand': name} for name in ('s1', 's2', 'd')],
                'operand_binding_required': True, 'context_binding': 'resolved',
                'numeric_values': [], 'ranges': [], 'comparisons': [], 'source_formulas': [],
                'unresolved_glyphs': [], 'unbound_condition_markers': [],
                'unparsed_input_qualification': False, 'device_state_conditions': [],
                'operand_device_conditions': [], 'cpu_models': list(CPU_MODELS),
                'knowledge_kind': 'manual_behavior', 'semantic_role': 'conditional_stop_event',
                'source_relation_identity': identity,
                'core_execution_capability': 'not_established',
                'boundary_composition_capability': 'not_established',
                'physical_stop_instant': 'not_established',
                'final_electrical_level': 'not_established',
                'binding_basis': 'AB_exact_individual_clause_and_ON_OFF_diagram'}}
    if branch == 'early_stop':
        rule['effect'] = {'stop_event': 'after_documented_nominal_delay',
                          'delay': {'value': 5, 'unit': 'microseconds',
                                    'anchor': 'output_stop_command',
                                    'interpretation': 'documented_nominal_not_physical_tolerance'}}
        result['additional_sources'] = [
            {'manual_id': 'fx5_programming_zh_y', 'manual_number': 'JY997D58901',
             'revision': 'Y', 'pdf_pages': [CHINESE_PAGES[block['page']], 1402]}]
        result['value_updates']['binding_basis'] = 'AB_command_anchor_and_same_edition_Chinese_Y_rendered_delay'
    if identity == 'condition_precautions_p824_r5_c0':
        if '[FX5S/FX5UJ CPU module]' not in text or '[FX5U/FX5UC CPU module and high-speed pulse input/output module]' not in text:
            return None
        # This source clause includes a complete hardware table before the stop
        # sentence. Preserve the existing separate table fact, not just timing.
        flat_clause = re.sub(r'\s+', ' ', clause).strip()
        table = flat_clause[flat_clause.index('Output number'):flat_clause.index(' Period Output stop command If')]
        result['split_pwm_hardware_table'] = True
        result['split_pwm_hardware_rows'] = parse_pwm_hardware_table(table)
    return result
